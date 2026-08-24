"""Ingestion pipeline — Phase 2 fake implementation.

The real parse/chunk/embed/index stages arrive in Phases 3-7. This module
provides a deterministic, DB-session-driven state-machine runner so the
worker, API and tests can exercise the full lifecycle today:

    queued -> parsing -> chunking -> embedding -> indexing -> finalizing -> ready

It acquires a PostgreSQL advisory lock keyed on the document id (so the same
document never has two concurrent write jobs), is idempotent (re-running a
succeeded ingest is a no-op), and on failure marks the job failed while
preserving any prior active DocumentVersion + IndexSnapshot.

The pipeline functions take an ``AsyncSession`` so they are trivially unit-
testable against SQLite (advisory lock skipped on non-PG dialects).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.chunk import DocumentVersion
from app.models.document import Document
from app.models.enums import (
    DocumentStatus,
    DocumentVersionStatus,
    IndexSnapshotStatus,
    JobStage,
    JobStatus,
)
from app.models.index_snapshot import IndexSnapshot, SystemState
from app.models.job import IngestionJob

logger = get_logger(__name__)


class FileRemover(Protocol):
    def __call__(self, stored_filename: str) -> None: ...


# Stable parser/chunk config marker for the Phase 2 fake.
FAKE_PARSER_VERSION = "fake-1.0"
FAKE_CHUNK_CONFIG = {
    "small_document_not_chunk": True,
    "small_document_char_threshold": 2048,
    "max_chunk_chars": 800,
    "sentence_merge_num": 6,
    "sentence_on": True,
    "table_on": True,
    "title_chunk_on": True,
    "need_chapter": False,
    "code_not_add_index": False,
    "retrieval_content_max_chars": 30000,
    "md_heading_max_level": 10,
    "neighbor_window": 1,
}


class PipelineError(Exception):
    """Base for recoverable pipeline failures carrying a stable code."""

    code = "PIPELINE_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class FakeParser(Protocol):
    def parse(self, document: Document) -> tuple[int, int]: ...


class FakeChunker(Protocol):
    def chunk(self, document: Document, version: DocumentVersion) -> int: ...


class _DefaultFakeParser:
    def parse(self, document: Document) -> tuple[int, int]:
        # page_count, character_count — Phase 2 placeholder.
        return 1, max(1, document.file_size)


class _DefaultFakeChunker:
    def chunk(self, document: Document, version: DocumentVersion) -> int:
        # chunk_count — Phase 2 placeholder (no real chunks written).
        return 0


class RealChunker:
    """Runs the deterministic chunking pipeline against a ParsedDocument.

    Writes Chunk ORM rows for the given DocumentVersion and returns the count.
    The parser must have produced a ParsedDocument available via ``parsed_doc``.
    """

    def __init__(self, parsed_doc: Any) -> None:
        self._parsed = parsed_doc

    def chunk(self, document: Document, version: DocumentVersion) -> int:
        from app.chunking.models import ChunkConfig
        from app.chunking.pipeline import chunk_document
        from app.models.chunk import Chunk as ChunkORM
        from app.models.enums import ChunkKind

        results = chunk_document(self._parsed, ChunkConfig.default())
        kind_map = {
            "text": ChunkKind.TEXT,
            "title": ChunkKind.TITLE,
            "table": ChunkKind.TABLE,
            "code": ChunkKind.CODE,
            "chapter": ChunkKind.CHAPTER,
        }
        for r in results:
            orm = ChunkORM(
                document_id=document.id,
                document_version_id=version.id,
                chunk_index=r.chunk_index,
                kind=kind_map[r.kind],
                section_path=r.section_path,
                raw_content=r.raw_content,
                retrieval_content=r.retrieval_content,
                content_hash=r.content_hash,
                character_count=r.character_count,
                page_start=r.page_start,
                page_end=r.page_end,
                line_start=r.line_start,
                line_end=r.line_end,
                metadata_=r.metadata or None,
            )
            version.chunks.append(orm)
        return len(results)


async def _pg_advisory_lock(session: AsyncSession, key: int) -> None:
    bind = session.bind
    dialect = bind.dialect.name if bind is not None else ""
    if dialect == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": key})


def _doc_lock_key(document_id: uuid.UUID) -> int:
    # Fold the UUID's top 64 bits into a non-negative int64 for pg_advisory_xact_lock.
    raw = document_id.bytes
    hi = int.from_bytes(raw[:8], "big", signed=False) & ((1 << 63) - 1)
    return hi


async def _set_job(
    session: AsyncSession,
    job: IngestionJob,
    *,
    status: JobStatus,
    stage: JobStage,
    progress: int,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    job.status = status
    job.stage = stage
    job.progress = max(job.progress, progress)
    if error_code is not None:
        job.error_code = error_code
    if error_message is not None:
        job.error_message = error_message
    if status == JobStatus.RUNNING and job.started_at is None:
        job.started_at = datetime.now(UTC)
    if status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
        job.finished_at = datetime.now(UTC)
    await session.flush()


async def _set_doc_status(
    document: Document, status: DocumentStatus, message: str | None = None
) -> None:
    document.status = status
    document.status_message = message


async def run_ingest(
    session: AsyncSession,
    job: IngestionJob,
    document: Document,
    *,
    parser: FakeParser = _DefaultFakeParser(),
    chunker: FakeChunker = _DefaultFakeChunker(),
    embedding_provider: Any | None = None,
    indexes_dir: Path | None = None,
) -> None:
    """Drive one ingest/reindex job through the full state machine.

    Idempotent: a job already succeeded is a no-op. On any ``PipelineError``
    the job is marked failed and the document's prior active version/snapshot
    are left intact.

    When ``embedding_provider`` is provided and chunks are produced, the
    embedding + indexing stages run: vectors are generated, a FAISS index is
    built, saved to a shadow path, validated via manifest, and atomically
    activated as a new IndexSnapshot.
    """
    if job.status == JobStatus.SUCCEEDED:
        return
    await _pg_advisory_lock(session, _doc_lock_key(document.id))
    await _set_job(session, job, status=JobStatus.RUNNING, stage=JobStage.QUEUED, progress=5)
    await _set_doc_status(document, DocumentStatus.PARSING)
    await _set_job(session, job, status=JobStatus.RUNNING, stage=JobStage.PARSING, progress=20)
    try:
        page_count, char_count = parser.parse(document)
        document.page_count = page_count
        document.character_count = char_count

        await _set_doc_status(document, DocumentStatus.CHUNKING)
        await _set_job(session, job, status=JobStatus.RUNNING, stage=JobStage.CHUNKING, progress=45)

        version = DocumentVersion(
            document_id=document.id,
            status=DocumentVersionStatus.BUILDING,
            parser_version=FAKE_PARSER_VERSION,
            chunk_config=FAKE_CHUNK_CONFIG,
        )
        session.add(version)
        await session.flush()

        chunk_count = chunker.chunk(document, version)
        version.chunk_count = chunk_count
        version.character_count = char_count

        # Embedding stage (spec §13.1): embed chunkable chunks and build FAISS.
        faiss_id_map: dict[int, uuid.UUID] = {}
        if embedding_provider is not None and chunk_count > 0:
            await _set_doc_status(document, DocumentStatus.EMBEDDING)
            await _set_job(
                session, job, status=JobStatus.RUNNING, stage=JobStage.EMBEDDING, progress=70
            )

            from sqlalchemy import func as sa_func
            from sqlalchemy import select as sa_select

            from app.index.faiss_index import FaissIndex
            from app.index.snapshot import (
                build_manifest,
                save_manifest,
                validate_manifest,
            )
            from app.models.chunk import Chunk

            manifest_emb = embedding_provider.manifest
            version.embedding_model_id = manifest_emb.model_id
            version.embedding_revision = manifest_emb.revision
            version.embedding_dimension = manifest_emb.dimension
            version.embedding_signature = manifest_emb.signature

            result_chunks = await session.execute(
                sa_select(Chunk)
                .where(Chunk.document_version_id == version.id)
                .order_by(Chunk.chunk_index)
            )
            chunkable = [
                c
                for c in result_chunks.scalars().all()
                if not (c.metadata_ and c.metadata_.get("code_not_add_index"))
            ]

            if chunkable:
                texts = [c.retrieval_content for c in chunkable]
                emb_result = embedding_provider.embed_texts(texts, is_query=False)

                max_id_result = await session.execute(sa_select(sa_func.max(Chunk.faiss_id)))
                max_faiss_id = max_id_result.scalar() or -1
                faiss_ids = np.arange(
                    max_faiss_id + 1, max_faiss_id + 1 + len(chunkable), dtype=np.int64
                )
                for i, c in enumerate(chunkable):
                    c.faiss_id = int(faiss_ids[i])
                    c.token_count = None
                    faiss_id_map[int(faiss_ids[i])] = c.id

                faiss_idx = FaissIndex.create(manifest_emb.dimension)
                faiss_idx.add_texts(emb_result.vectors, faiss_ids, normalize=False)

                await _set_doc_status(document, DocumentStatus.INDEXING)
                await _set_job(
                    session, job, status=JobStatus.RUNNING, stage=JobStage.INDEXING, progress=90
                )

                if indexes_dir is None:
                    indexes_dir = Path("./storage/indexes")
                snap_dir = indexes_dir / str(version.id)
                snap_dir.mkdir(parents=True, exist_ok=True)
                faiss_path = snap_dir / "index.faiss"
                manifest_path = snap_dir / "manifest.json"
                faiss_idx.save(faiss_path)

                manifest = build_manifest(
                    manifest_embedding=manifest_emb,
                    faiss_path=faiss_path,
                    document_versions={str(document.id): str(version.id)},
                    document_count=1,
                    chunk_count=len(chunkable),
                    max_faiss_id=int(faiss_ids[-1]) if len(faiss_ids) > 0 else 0,
                )
                save_manifest(manifest, manifest_path)
                validate_manifest(
                    manifest,
                    faiss_path=faiss_path,
                    expected_embedding_signature=manifest_emb.signature,
                    expected_dimension=manifest_emb.dimension,
                )

                snapshot = IndexSnapshot(
                    status=IndexSnapshotStatus.ACTIVE,
                    embedding_signature=manifest_emb.signature,
                    faiss_path=str(faiss_path),
                    manifest_sha256=manifest.sha256,
                    manifest=manifest.to_dict(),
                    document_count=1,
                    chunk_count=len(chunkable),
                    max_faiss_id=int(faiss_ids[-1]) if len(faiss_ids) > 0 else 0,
                    activated_at=datetime.now(UTC),
                )
                session.add(snapshot)
                await session.flush()

                # Update SystemState singleton.
                sys_state = await session.get(SystemState, 1)
                if sys_state is None:
                    sys_state = SystemState(id=1)
                    session.add(sys_state)
                old_snapshot_id = sys_state.active_index_snapshot_id
                if old_snapshot_id is not None:
                    old = await session.get(IndexSnapshot, old_snapshot_id)
                    if old is not None and old.status == IndexSnapshotStatus.ACTIVE:
                        old.status = IndexSnapshotStatus.SUPERSEDED
                sys_state.active_index_snapshot_id = snapshot.id
        else:
            await _set_doc_status(document, DocumentStatus.EMBEDDING)
            await _set_job(
                session, job, status=JobStatus.RUNNING, stage=JobStage.EMBEDDING, progress=70
            )
            await _set_doc_status(document, DocumentStatus.INDEXING)
            await _set_job(
                session, job, status=JobStatus.RUNNING, stage=JobStage.INDEXING, progress=90
            )

        # Finalize: mark version ready and atomically switch the document pointer.
        version.status = DocumentVersionStatus.READY
        document.active_document_version_id = version.id
        document.parser_version = FAKE_PARSER_VERSION
        document.chunk_count = chunk_count
        await _set_doc_status(document, DocumentStatus.READY)
        await _set_job(
            session, job, status=JobStatus.RUNNING, stage=JobStage.FINALIZING, progress=99
        )
        await _set_job(
            session, job, status=JobStatus.SUCCEEDED, stage=JobStage.FINALIZING, progress=100
        )
        logger.info(
            "ingest_succeeded",
            document_id=str(document.id),
            job_id=str(job.id),
            chunk_count=chunk_count,
        )
    except PipelineError as exc:
        await _set_job(
            session,
            job,
            status=JobStatus.FAILED,
            stage=job.stage,
            progress=job.progress,
            error_code=exc.code,
            error_message=str(exc),
        )
        await _set_doc_status(document, DocumentStatus.FAILED, str(exc))
        logger.warning(
            "ingest_failed",
            document_id=str(document.id),
            job_id=str(job.id),
            code=exc.code,
        )
    except Exception as exc:
        await _set_job(
            session,
            job,
            status=JobStatus.FAILED,
            stage=job.stage,
            progress=job.progress,
            error_code="INTERNAL_ERROR",
            error_message=type(exc).__name__,
        )
        await _set_doc_status(document, DocumentStatus.FAILED, type(exc).__name__)
        logger.exception("ingest_crashed", document_id=str(document.id), job_id=str(job.id))


async def run_delete_cleanup(
    session: AsyncSession,
    job: IngestionJob,
    document: Document,
    *,
    file_remover: FileRemover,
) -> None:
    """Mark a document deleted and remove its upload file.

    ``file_remover`` is callable ``(stored_filename) -> None`` so tests can
    inject a fake without touching the filesystem.
    """
    await _pg_advisory_lock(session, _doc_lock_key(document.id))
    await _set_job(session, job, status=JobStatus.RUNNING, stage=JobStage.FINALIZING, progress=10)
    try:
        stored = document.stored_filename
        # Remove from all collections (association cascade handles DB rows,
        # but we delete explicitly so the count is correct even pre-flush).
        from sqlalchemy import delete as sa_delete

        from app.models.collection import CollectionDocument

        await session.execute(
            sa_delete(CollectionDocument).where(CollectionDocument.document_id == document.id)
        )
        # Mark deleted (row remains for audit; hard delete deferred to compaction).
        await _set_doc_status(document, DocumentStatus.DELETED)
        document.active_document_version_id = None
        await _set_job(
            session, job, status=JobStatus.RUNNING, stage=JobStage.FINALIZING, progress=80
        )
        file_remover(stored)
        await _set_job(
            session, job, status=JobStatus.SUCCEEDED, stage=JobStage.FINALIZING, progress=100
        )
        logger.info("delete_succeeded", document_id=str(document.id), job_id=str(job.id))
    except Exception as exc:
        await _set_job(
            session,
            job,
            status=JobStatus.FAILED,
            stage=JobStage.FINALIZING,
            progress=job.progress,
            error_code="DELETE_FAILED",
            error_message=type(exc).__name__,
        )
        # Keep document in `deleting` so it can be retried.
        await _set_doc_status(document, DocumentStatus.DELETING, type(exc).__name__)
        logger.exception("delete_failed", document_id=str(document.id), job_id=str(job.id))


def _dispatch_kind(job: IngestionJob) -> str:
    return job.kind.value if hasattr(job.kind, "value") else str(job.kind)


__all__ = [
    "FAKE_CHUNK_CONFIG",
    "FAKE_PARSER_VERSION",
    "FileRemover",
    "PipelineError",
    "run_delete_cleanup",
    "run_ingest",
]

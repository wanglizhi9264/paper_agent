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
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.chunk import DocumentVersion
from app.models.document import Document
from app.models.enums import (
    DocumentStatus,
    DocumentVersionStatus,
    JobStage,
    JobStatus,
)
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
) -> None:
    """Drive one ingest/reindex job through the full state machine.

    Idempotent: a job already succeeded is a no-op. On any ``PipelineError``
    the job is marked failed and the document's prior active version/snapshot
    are left intact.
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

        await _set_doc_status(document, DocumentStatus.EMBEDDING)
        await _set_job(
            session, job, status=JobStatus.RUNNING, stage=JobStage.EMBEDDING, progress=70
        )

        await _set_doc_status(document, DocumentStatus.INDEXING)
        await _set_job(session, job, status=JobStatus.RUNNING, stage=JobStage.INDEXING, progress=90)

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

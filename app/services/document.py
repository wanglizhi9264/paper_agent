from __future__ import annotations

import contextlib
import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import (
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from app.core.config import Settings
from app.core.ids import new_uuid
from app.core.logging import get_logger
from app.core.security import sanitize_display_filename
from app.models.chunk import Chunk
from app.models.collection import CollectionDocument
from app.models.document import Document
from app.models.enums import (
    ALLOWED_EXTENSIONS,
    ALLOWED_MEDIA_TYPES,
    DocumentStatus,
    JobKind,
    JobStage,
    JobStatus,
)
from app.models.job import IngestionJob
from app.services.enqueuer import TaskEnqueuer

logger = get_logger(__name__)

# Magic-byte sniffing for extension+content cross-validation (spec §17).
_MAGIC_SNIFF = {
    "pdf": (b"%PDF-",),
    "docx": (b"PK\x03\x04",),
    "md": (),  # text; no magic, validated by extension + decode roundtrip only
}


class DocumentService:
    """Upload, list, fetch, reindex and delete documents.

    Routes call this; transactions commit at the FastAPI dependency boundary
    (``get_session``). Long parsing/indexing never runs here — only DB writes
    and enqueue.
    """

    def __init__(self, settings: Settings, enqueuer: TaskEnqueuer) -> None:
        self._settings = settings
        self._enqueuer = enqueuer

    async def ingest_upload(
        self,
        session: AsyncSession,
        *,
        filename: str,
        stream: IO[bytes],
        content_type: str | None,
        collection_ids: list[uuid.UUID],
    ) -> tuple[Document, IngestionJob]:
        """Stream an upload to disk, create Document + IngestionJob, enqueue.

        Commit happens at the dependency boundary; if enqueue fails after commit,
        the job remains ``queued`` and reconciliation recovers it (Phase 9).
        """
        ext = self._validate_filename(filename)
        stored_name = f"{new_uuid().hex}.{ext}"
        tmp_path = self._settings.tmp_dir / stored_name
        size, sha = await self._stream_to_disk(stream, tmp_path, ext, content_type)
        self._atomic_move(tmp_path, self._settings.uploads_dir / stored_name)

        title = Path(filename).stem or filename
        doc = Document(
            filename=sanitize_display_filename(filename),
            stored_filename=stored_name,
            media_type=ALLOWED_MEDIA_TYPES[ext],
            extension=ext,
            title=title,
            sha256=sha,
            file_size=size,
            status=DocumentStatus.QUEUED,
        )
        session.add(doc)
        await session.flush()

        for cid in collection_ids:
            await self._ensure_collection_exists(session, cid)
            session.add(CollectionDocument(collection_id=cid, document_id=doc.id))

        job = IngestionJob(
            document_id=doc.id,
            kind=JobKind.INGEST,
            status=JobStatus.QUEUED,
            stage=JobStage.QUEUED,
            attempt=1,
        )
        session.add(job)
        await session.flush()

        logger.info(
            "document_ingested",
            document_id=str(doc.id),
            job_id=str(job.id),
            extension=ext,
            size=size,
        )
        return doc, job

    async def enqueue_after_commit(self, job: IngestionJob) -> None:
        await self._enqueuer.enqueue(
            str(job.id),
            job.kind.value,
            document_id=str(job.document_id),
            attempt=job.attempt,
        )

    async def list_documents(
        self,
        session: AsyncSession,
        *,
        status: str | None,
        collection_id: uuid.UUID | None,
        limit: int,
        cursor_id: uuid.UUID | None,
    ) -> tuple[list[Document], bool]:
        stmt = select(Document).order_by(Document.created_at.desc(), Document.id.desc())
        if status:
            stmt = stmt.where(Document.status == status)
        if collection_id is not None:
            stmt = stmt.join(
                CollectionDocument, CollectionDocument.document_id == Document.id
            ).where(CollectionDocument.collection_id == collection_id)
        if cursor_id is not None:
            stmt = stmt.where(Document.id < cursor_id)
        stmt = stmt.limit(limit + 1)
        rows = (await session.execute(stmt)).scalars().all()
        has_more = len(rows) > limit
        return list(rows[:limit]), has_more

    async def get_document(self, session: AsyncSession, document_id: uuid.UUID) -> Document:
        doc = await session.get(Document, document_id)
        if doc is None or doc.status == DocumentStatus.DELETED:
            raise NotFoundError(code="DOCUMENT_NOT_FOUND", message="Document was not found.")
        return doc

    async def get_chunks(
        self,
        session: AsyncSession,
        document_id: uuid.UUID,
        *,
        kind: str | None,
        limit: int,
        cursor_index: int | None,
    ) -> tuple[list[Chunk], bool]:
        doc = await self.get_document(session, document_id)
        if doc.active_document_version_id is None:
            return [], False
        stmt = (
            select(Chunk)
            .where(Chunk.document_version_id == doc.active_document_version_id)
            .order_by(Chunk.chunk_index.asc())
        )
        if kind:
            stmt = stmt.where(Chunk.kind == kind)
        if cursor_index is not None:
            stmt = stmt.where(Chunk.chunk_index > cursor_index)
        stmt = stmt.limit(limit + 1)
        rows = (await session.execute(stmt)).scalars().all()
        has_more = len(rows) > limit
        return list(rows[:limit]), has_more

    async def request_reindex(
        self,
        session: AsyncSession,
        document_id: uuid.UUID,
    ) -> IngestionJob:
        doc = await self.get_document(session, document_id)
        await self._reject_if_write_running(session, document_id)
        job = IngestionJob(
            document_id=doc.id,
            kind=JobKind.REINDEX,
            status=JobStatus.QUEUED,
            stage=JobStage.QUEUED,
            attempt=1,
        )
        session.add(job)
        doc.status = DocumentStatus.QUEUED
        doc.status_message = None
        await session.flush()
        return job

    async def request_delete(
        self,
        session: AsyncSession,
        document_id: uuid.UUID,
    ) -> IngestionJob:
        doc = await self.get_document(session, document_id)
        # Idempotent: if a delete_cleanup job is already running, return it.
        existing = await self._find_active_job(session, document_id, JobKind.DELETE_CLEANUP)
        if existing is not None:
            return existing
        job = IngestionJob(
            document_id=doc.id,
            kind=JobKind.DELETE_CLEANUP,
            status=JobStatus.QUEUED,
            stage=JobStage.QUEUED,
            attempt=1,
        )
        session.add(job)
        doc.status = DocumentStatus.DELETING
        doc.status_message = None
        await session.flush()
        return job

    async def retry_job(self, session: AsyncSession, job_id: uuid.UUID) -> IngestionJob:
        job = await session.get(IngestionJob, job_id)
        if job is None:
            raise NotFoundError(code="JOB_NOT_FOUND", message="Job was not found.")
        if job.status != JobStatus.FAILED:
            raise ConflictError(
                code="JOB_NOT_RETRYABLE",
                message="Only failed jobs can be retried.",
            )
        new_job = IngestionJob(
            document_id=job.document_id,
            kind=job.kind,
            status=JobStatus.QUEUED,
            stage=JobStage.QUEUED,
            attempt=job.attempt + 1,
        )
        session.add(new_job)
        doc = await session.get(Document, job.document_id)
        if doc is not None and doc.status not in (DocumentStatus.DELETED,):
            doc.status = DocumentStatus.QUEUED
            doc.status_message = None
        await session.flush()
        return new_job

    # --- internals ---

    def _validate_filename(self, filename: str) -> str:
        if not filename or "." not in filename:
            raise UnsupportedMediaTypeError(
                code="UNSUPPORTED_MEDIA_TYPE", message="Filename must have an extension."
            )
        ext = filename.rsplit(".", 1)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise UnsupportedMediaTypeError(
                code="UNSUPPORTED_MEDIA_TYPE",
                message=f"Extension '{ext}' is not supported. Allowed: pdf, docx, md.",
            )
        return ext

    async def _stream_to_disk(
        self, stream: IO[bytes], path: Path, ext: str, content_type: str | None
    ) -> tuple[int, str]:
        hasher = hashlib.sha256()
        size = 0
        sniff_buf = bytearray()
        max_sniff = 8
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            while True:
                chunk = stream.read(1 << 20)  # 1 MiB
                if not chunk:
                    break
                if size + len(chunk) > self._settings.max_upload_bytes:
                    f.close()
                    self._safe_unlink(path)
                    raise PayloadTooLargeError(
                        code="PAYLOAD_TOO_LARGE",
                        message="Upload exceeds the maximum allowed size.",
                    )
                if len(sniff_buf) < max_sniff:
                    sniff_buf.extend(chunk[: max_sniff - len(sniff_buf)])
                hasher.update(chunk)
                f.write(chunk)
                size += len(chunk)
        self._validate_magic(bytes(sniff_buf), ext, content_type)
        return size, hasher.hexdigest()

    def _validate_magic(self, sniff: bytes, ext: str, content_type: str | None) -> None:
        magics = _MAGIC_SNIFF[ext]
        if magics and not any(sniff.startswith(m) for m in magics):
            raise UnsupportedMediaTypeError(
                code="UNSUPPORTED_MEDIA_TYPE",
                message=f"File content does not match extension '{ext}'.",
            )

    def _atomic_move(self, src: Path, dst: Path) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dst)

    def _safe_unlink(self, path: Path) -> None:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()

    async def _ensure_collection_exists(self, session: AsyncSession, cid: uuid.UUID) -> None:
        from app.models.collection import Collection

        coll = await session.get(Collection, cid)
        if coll is None:
            raise NotFoundError(code="COLLECTION_NOT_FOUND", message="Collection was not found.")

    async def _reject_if_write_running(self, session: AsyncSession, document_id: uuid.UUID) -> None:
        for kind in (JobKind.INGEST, JobKind.REINDEX, JobKind.DELETE_CLEANUP):
            existing = await self._find_active_job(session, document_id, kind)
            if existing is not None:
                raise ConflictError(
                    code="DOCUMENT_BUSY",
                    message="Another write job is already running for this document.",
                )

    async def _find_active_job(
        self, session: AsyncSession, document_id: uuid.UUID, kind: JobKind
    ) -> IngestionJob | None:
        stmt = select(IngestionJob).where(
            IngestionJob.document_id == document_id,
            IngestionJob.kind == kind,
            IngestionJob.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)),
        )
        return (await session.execute(stmt)).scalars().first()


def now_utc() -> datetime:
    return datetime.now(UTC)

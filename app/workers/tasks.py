"""ARQ worker tasks.

Each task receives the DB ``job_id`` (== ARQ job id) plus the document id, loads
the job and document inside a fresh session, and dispatches to the ingestion
pipeline. The pipeline is DB-driven and dialect-aware (advisory lock is
skipped on non-PG dialects), so these tasks are exercised in unit tests via a
SQLite session.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from arq import Worker

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.models.document import Document
from app.models.enums import JobKind, JobStatus
from app.models.job import IngestionJob
from app.services.ingestion import FileRemover, run_delete_cleanup, run_ingest

logger = get_logger(__name__)


async def _load_job_and_document(
    session: AsyncSession, job_id: str
) -> tuple[IngestionJob, Document]:
    import uuid

    job = await session.get(IngestionJob, uuid.UUID(job_id))
    if job is None:
        raise RuntimeError(f"job {job_id} not found")
    document = await session.get(Document, job.document_id)
    if document is None:
        raise RuntimeError(f"document {job.document_id} not found")
    return job, document


async def ingestion_task(
    ctx: Worker,  # arq passes a WorkerContext at runtime
    job_id: str,
    *,
    document_id: str,
    attempt: int = 1,
    **_extra: Any,
) -> str:
    """Dispatch an ingest / reindex / delete_cleanup job."""
    async with session_scope() as session:
        job, document = await _load_job_and_document(session, job_id)
        if job.status == JobStatus.SUCCEEDED:
            return "noop"
        kind = job.kind.value if hasattr(job.kind, "value") else str(job.kind)
        if kind in (JobKind.INGEST.value, JobKind.REINDEX.value):
            await run_ingest(session, job, document)
        elif kind == JobKind.DELETE_CLEANUP.value:
            settings = get_settings()
            await run_delete_cleanup(
                session,
                job,
                document,
                file_remover=_make_file_remover(settings.uploads_dir),
            )
        else:
            raise RuntimeError(f"unknown job kind: {kind}")
        return job.status.value if hasattr(job.status, "value") else str(job.status)


def _make_file_remover(uploads_dir: Path) -> FileRemover:
    def _remove(stored_filename: str) -> None:
        path = uploads_dir / stored_filename
        try:
            path.unlink()
        except FileNotFoundError:
            logger.warning("upload_file_missing_on_delete", path=str(path))

    return _remove


# ARQ function registry.
functions = [ingestion_task]

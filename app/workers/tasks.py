"""ARQ worker tasks.

Each task receives the DB ``job_id`` (== ARQ job id) plus the document id, loads
the job and document inside a fresh session, and dispatches to the ingestion
pipeline. The pipeline is DB-driven and dialect-aware (advisory lock is
skipped on non-PG dialects), so these tasks are exercised in unit tests via a
SQLite session.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from arq import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.embedding.registry import get_embedding_provider
from app.models.document import Document
from app.models.enums import JobKind, JobStatus
from app.models.job import IngestionJob
from app.services.ingestion import (
    FileRemover,
    RealChunker,
    RealDocumentParser,
    V2PDFDocumentParser,
    run_delete_cleanup,
    run_ingest,
)

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
    ctx: dict[str, Any],
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
        kind = job.kind if type(job.kind) is str else job.kind.value
        if kind in (JobKind.INGEST.value, JobKind.REINDEX.value):
            settings = get_settings()
            artifact_manager = None
            parser: V2PDFDocumentParser | RealDocumentParser
            if document.extension == "pdf":
                from app.services.ir_artifacts import IRArtifactManager

                artifact_manager = IRArtifactManager(settings.storage_dir)
                parser = V2PDFDocumentParser(
                    settings.uploads_dir / document.stored_filename,
                    artifact_manager,
                    settings=settings,
                )
            else:
                parser = RealDocumentParser(
                    settings.uploads_dir / document.stored_filename,
                    document.extension,
                )
            await run_ingest(
                session,
                job,
                document,
                parser=parser,
                chunker=RealChunker(parser),
                embedding_provider=get_embedding_provider(settings),
                indexes_dir=settings.indexes_dir,
                artifact_manager=artifact_manager,
            )
        elif kind == JobKind.DELETE_CLEANUP.value:
            settings = get_settings()
            from app.services.ir_artifacts import IRArtifactManager

            artifact_manager = IRArtifactManager(settings.storage_dir)
            await run_delete_cleanup(
                session,
                job,
                document,
                file_remover=_make_file_remover(settings.uploads_dir),
                artifact_remover=artifact_manager.remove_version,
            )
        else:
            raise RuntimeError(f"unknown job kind: {kind}")
        return str(job.status if type(job.status) is str else job.status.value)


def _make_file_remover(uploads_dir: Path) -> FileRemover:
    def _remove(stored_filename: str) -> None:
        path = uploads_dir / stored_filename
        try:
            path.unlink()
        except FileNotFoundError:
            logger.warning("upload_file_missing_on_delete", path=str(path))

    return _remove


# ARQ function registry. The API queues a stable name per job kind while the
# dispatcher reads the authoritative kind from PostgreSQL.
functions = [
    func(ingestion_task, name=f"ingestion:{kind.value}")
    for kind in (JobKind.INGEST, JobKind.REINDEX, JobKind.DELETE_CLEANUP)
]

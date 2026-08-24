from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.document import Document
from app.models.enums import (
    DocumentStatus,
    JobKind,
    JobStage,
    JobStatus,
)
from app.models.job import IngestionJob
from app.services.ingestion import PipelineError, run_delete_cleanup, run_ingest


def _make_doc_and_job(
    status: DocumentStatus = DocumentStatus.QUEUED,
    kind: JobKind = JobKind.INGEST,
) -> tuple[Document, IngestionJob]:
    did = uuid.uuid4()
    doc = Document(
        id=did,
        filename="p.pdf",
        stored_filename="s.pdf",
        media_type="application/pdf",
        extension="pdf",
        sha256="a" * 64,
        file_size=128,
        status=status,
    )
    job = IngestionJob(
        id=uuid.uuid4(),
        document_id=did,
        kind=kind,
        status=JobStatus.QUEUED,
        stage=JobStage.QUEUED,
        attempt=1,
    )
    return doc, job


@pytest.mark.asyncio
async def test_run_ingest_full_state_machine(async_sqlite_session) -> None:
    doc, job = _make_doc_and_job()
    async_sqlite_session.add_all([doc, job])
    await async_sqlite_session.flush()

    await run_ingest(async_sqlite_session, job, doc)
    await async_sqlite_session.commit()

    await async_sqlite_session.refresh(job)
    await async_sqlite_session.refresh(doc)
    assert job.status == JobStatus.SUCCEEDED
    assert job.stage == JobStage.FINALIZING
    assert job.progress == 100
    assert job.started_at is not None
    assert job.finished_at is not None
    assert doc.status == DocumentStatus.READY
    assert doc.active_document_version_id is not None
    assert doc.parser_version is not None
    assert doc.chunk_count >= 0


@pytest.mark.asyncio
async def test_run_ingest_idempotent_on_succeeded(async_sqlite_session) -> None:
    doc, job = _make_doc_and_job(status=DocumentStatus.READY)
    job.status = JobStatus.SUCCEEDED
    job.progress = 100
    async_sqlite_session.add_all([doc, job])
    await async_sqlite_session.flush()

    progress_before = job.progress
    await run_ingest(async_sqlite_session, job, doc)
    await async_sqlite_session.commit()
    assert job.progress == progress_before  # no re-run


@pytest.mark.asyncio
async def test_run_ingest_failure_marks_failed_keeps_old_version(async_sqlite_session) -> None:
    doc, job = _make_doc_and_job()
    async_sqlite_session.add_all([doc, job])
    await async_sqlite_session.flush()

    class _Bomb:
        def parse(self, document: Document) -> tuple[int, int]:
            raise PipelineError("bad pdf", code="PARSE_ERROR")

    await run_ingest(async_sqlite_session, job, doc, parser=_Bomb())  # type: ignore[arg-type]
    await async_sqlite_session.commit()

    await async_sqlite_session.refresh(job)
    await async_sqlite_session.refresh(doc)
    assert job.status == JobStatus.FAILED
    assert job.error_code == "PARSE_ERROR"
    assert doc.status == DocumentStatus.FAILED
    # No new active version on failure
    assert doc.active_document_version_id is None


@pytest.mark.asyncio
async def test_run_delete_cleanup_removes_file_and_associations(
    async_sqlite_session, tmp_path
) -> None:
    from app.models.collection import Collection, CollectionDocument

    doc, job = _make_doc_and_job(status=DocumentStatus.DELETING, kind=JobKind.DELETE_CLEANUP)
    job.status = JobStatus.QUEUED
    coll = Collection(id=uuid.uuid4(), name="C", description="")
    async_sqlite_session.add_all([doc, job, coll])
    await async_sqlite_session.flush()
    async_sqlite_session.add(CollectionDocument(collection_id=coll.id, document_id=doc.id))
    await async_sqlite_session.flush()

    removed: list[str] = []

    def _remover(stored: str) -> None:
        removed.append(stored)

    await run_delete_cleanup(async_sqlite_session, job, doc, file_remover=_remover)
    await async_sqlite_session.commit()

    await async_sqlite_session.refresh(job)
    await async_sqlite_session.refresh(doc)
    assert job.status == JobStatus.SUCCEEDED
    assert doc.status == DocumentStatus.DELETED
    assert removed == [doc.stored_filename]
    assert doc.active_document_version_id is None

    remaining = (
        (
            await async_sqlite_session.execute(
                select(CollectionDocument).where(CollectionDocument.document_id == doc.id)
            )
        )
        .scalars()
        .all()
    )
    assert remaining == []


@pytest.mark.asyncio
async def test_run_delete_cleanup_failure_keeps_deleting(async_sqlite_session) -> None:
    doc, job = _make_doc_and_job(status=DocumentStatus.DELETING, kind=JobKind.DELETE_CLEANUP)
    job.status = JobStatus.QUEUED
    async_sqlite_session.add_all([doc, job])
    await async_sqlite_session.flush()

    def _boom(_stored: str) -> None:
        raise RuntimeError("disk on fire")

    await run_delete_cleanup(async_sqlite_session, job, doc, file_remover=_boom)
    await async_sqlite_session.commit()

    await async_sqlite_session.refresh(job)
    await async_sqlite_session.refresh(doc)
    assert job.status == JobStatus.FAILED
    assert job.error_code == "DELETE_FAILED"
    # Document stays in `deleting` so it can be retried.
    assert doc.status == DocumentStatus.DELETING

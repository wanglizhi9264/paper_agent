from __future__ import annotations

import io

import pytest

from app.api.errors import ConflictError, NotFoundError, UnsupportedMediaTypeError
from app.core.config import Settings
from app.models.enums import DocumentStatus, JobKind, JobStatus
from app.schemas.collection import CollectionCreate, CollectionUpdate
from app.services.arq_enqueuer import FakeEnqueuer
from app.services.collection import CollectionService
from app.services.document import DocumentService


def _settings(tmp_path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        database_url="postgresql+asyncpg://u:p@127.0.0.1/db",
        redis_url="redis://127.0.0.1:6379/0",
        storage_dir=tmp_path,
    )


def _pdf_stream(content: bytes = b"%PDF-1.4\nhello world\n"):
    return io.BytesIO(content)


@pytest.mark.asyncio
async def test_ingest_upload_creates_doc_and_job(async_sqlite_session, tmp_path) -> None:
    settings = _settings(tmp_path)
    settings.ensure_storage_dirs()
    enq = FakeEnqueuer()
    service = DocumentService(settings, enq)

    doc, job = await service.ingest_upload(
        async_sqlite_session,
        filename="paper.pdf",
        stream=_pdf_stream(),
        content_type="application/pdf",
        collection_ids=[],
    )
    await async_sqlite_session.commit()

    assert doc.status == DocumentStatus.QUEUED
    assert doc.extension == "pdf"
    assert doc.file_size > 0
    assert len(doc.sha256) == 64
    assert job.kind == JobKind.INGEST
    assert job.status == JobStatus.QUEUED
    # File moved to uploads
    assert (settings.uploads_dir / doc.stored_filename).exists()
    assert not (settings.tmp_dir / doc.stored_filename).exists()


@pytest.mark.asyncio
async def test_ingest_upload_rejects_bad_extension(async_sqlite_session, tmp_path) -> None:
    settings = _settings(tmp_path)
    service = DocumentService(settings, FakeEnqueuer())
    with pytest.raises(UnsupportedMediaTypeError):
        await service.ingest_upload(
            async_sqlite_session,
            filename="paper.exe",
            stream=_pdf_stream(),
            content_type="application/octet-stream",
            collection_ids=[],
        )


@pytest.mark.asyncio
async def test_ingest_upload_rejects_magic_mismatch(async_sqlite_session, tmp_path) -> None:
    settings = _settings(tmp_path)
    service = DocumentService(settings, FakeEnqueuer())
    # .pdf but content not starting with %PDF-
    with pytest.raises(UnsupportedMediaTypeError):
        await service.ingest_upload(
            async_sqlite_session,
            filename="fake.pdf",
            stream=io.BytesIO(b"not a pdf"),
            content_type="application/pdf",
            collection_ids=[],
        )


@pytest.mark.asyncio
async def test_ingest_upload_enforces_max_size(async_sqlite_session, tmp_path) -> None:
    settings = Settings(  # type: ignore[call-arg]
        database_url="postgresql+asyncpg://u:p@127.0.0.1/db",
        redis_url="redis://127.0.0.1:6379/0",
        storage_dir=tmp_path,
        max_upload_bytes=4,
    )
    settings.ensure_storage_dirs()
    service = DocumentService(settings, FakeEnqueuer())
    from app.api.errors import PayloadTooLargeError

    with pytest.raises(PayloadTooLargeError):
        await service.ingest_upload(
            async_sqlite_session,
            filename="big.pdf",
            stream=io.BytesIO(b"%PDF-" + b"x" * 100),
            content_type="application/pdf",
            collection_ids=[],
        )


@pytest.mark.asyncio
async def test_request_reindex_conflicts_with_running_job(async_sqlite_session, tmp_path) -> None:
    settings = _settings(tmp_path)
    service = DocumentService(settings, FakeEnqueuer())
    doc, _ = await service.ingest_upload(
        async_sqlite_session,
        filename="p.pdf",
        stream=_pdf_stream(),
        content_type="application/pdf",
        collection_ids=[],
    )
    await async_sqlite_session.commit()
    with pytest.raises(ConflictError):
        await service.request_reindex(async_sqlite_session, doc.id)


@pytest.mark.asyncio
async def test_request_delete_is_idempotent(async_sqlite_session, tmp_path) -> None:
    settings = _settings(tmp_path)
    service = DocumentService(settings, FakeEnqueuer())
    doc, _ = await service.ingest_upload(
        async_sqlite_session,
        filename="p.pdf",
        stream=_pdf_stream(),
        content_type="application/pdf",
        collection_ids=[],
    )
    await async_sqlite_session.commit()
    j1 = await service.request_delete(async_sqlite_session, doc.id)
    await async_sqlite_session.commit()
    j2 = await service.request_delete(async_sqlite_session, doc.id)
    await async_sqlite_session.commit()
    assert j1.id == j2.id
    assert doc.status == DocumentStatus.DELETING


@pytest.mark.asyncio
async def test_retry_rejects_non_failed_job(async_sqlite_session, tmp_path) -> None:
    settings = _settings(tmp_path)
    service = DocumentService(settings, FakeEnqueuer())
    doc, job = await service.ingest_upload(
        async_sqlite_session,
        filename="p.pdf",
        stream=_pdf_stream(),
        content_type="application/pdf",
        collection_ids=[],
    )
    await async_sqlite_session.commit()
    with pytest.raises(ConflictError):
        await service.retry_job(async_sqlite_session, job.id)


@pytest.mark.asyncio
async def test_get_document_404_on_deleted(async_sqlite_session, tmp_path) -> None:
    settings = _settings(tmp_path)
    service = DocumentService(settings, FakeEnqueuer())
    doc, _ = await service.ingest_upload(
        async_sqlite_session,
        filename="p.pdf",
        stream=_pdf_stream(),
        content_type="application/pdf",
        collection_ids=[],
    )
    await async_sqlite_session.commit()
    doc.status = DocumentStatus.DELETED
    await async_sqlite_session.commit()
    with pytest.raises(NotFoundError):
        await service.get_document(async_sqlite_session, doc.id)


@pytest.mark.asyncio
async def test_collection_crud(async_sqlite_session) -> None:
    service = CollectionService()
    coll = await service.create(async_sqlite_session, CollectionCreate(name="Set A"))
    await async_sqlite_session.commit()
    assert coll.name == "Set A"

    fetched = await service.get(async_sqlite_session, coll.id)
    assert fetched.id == coll.id

    updated = await service.update(
        async_sqlite_session, coll.id, CollectionUpdate(name="Set A2", description="d")
    )
    await async_sqlite_session.commit()
    assert updated.name == "Set A2"
    assert updated.description == "d"

    await service.delete(async_sqlite_session, coll.id)
    await async_sqlite_session.commit()
    with pytest.raises(NotFoundError):
        await service.get(async_sqlite_session, coll.id)


@pytest.mark.asyncio
async def test_collection_duplicate_name_rejected(async_sqlite_session) -> None:
    service = CollectionService()
    await service.create(async_sqlite_session, CollectionCreate(name="dup"))
    await async_sqlite_session.commit()
    with pytest.raises(ConflictError):
        await service.create(async_sqlite_session, CollectionCreate(name="dup"))


@pytest.mark.asyncio
async def test_collection_add_remove_document(async_sqlite_session, tmp_path) -> None:
    coll_service = CollectionService()
    doc_service = DocumentService(_settings(tmp_path), FakeEnqueuer())
    coll = await coll_service.create(async_sqlite_session, CollectionCreate(name="C"))
    doc, _ = await doc_service.ingest_upload(
        async_sqlite_session,
        filename="p.pdf",
        stream=_pdf_stream(),
        content_type="application/pdf",
        collection_ids=[],
    )
    await async_sqlite_session.commit()

    await coll_service.add_document(async_sqlite_session, coll.id, doc.id)
    await async_sqlite_session.commit()
    assert await coll_service.document_count(async_sqlite_session, coll.id) == 1

    # idempotent
    await coll_service.add_document(async_sqlite_session, coll.id, doc.id)
    await async_sqlite_session.commit()
    assert await coll_service.document_count(async_sqlite_session, coll.id) == 1

    await coll_service.remove_document(async_sqlite_session, coll.id, doc.id)
    await async_sqlite_session.commit()
    assert await coll_service.document_count(async_sqlite_session, coll.id) == 0

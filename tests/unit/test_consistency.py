from __future__ import annotations

import uuid

import pytest

from app.models.chunk import DocumentVersion
from app.models.document import Document
from app.models.enums import (
    DocumentStatus,
    DocumentVersionStatus,
    IndexSnapshotStatus,
    JobKind,
    JobStage,
    JobStatus,
)
from app.models.index_snapshot import IndexSnapshot, SystemState
from app.models.job import IngestionJob
from app.services.consistency import (
    check_document_consistency,
    check_index_health,
    reconcile_stale_jobs,
)


def _make_doc(session, status=DocumentStatus.READY) -> Document:
    doc = Document(
        id=uuid.uuid4(),
        filename="p.pdf",
        stored_filename="s.pdf",
        media_type="application/pdf",
        extension="pdf",
        sha256="a" * 64,
        file_size=128,
        status=status,
    )
    session.add(doc)
    return doc


@pytest.mark.asyncio
async def test_check_index_health_no_snapshot(async_sqlite_session) -> None:
    result = await check_index_health(async_sqlite_session)
    assert result.healthy is True
    assert result.error_code == "NOT_INITIALIZED"


@pytest.mark.asyncio
async def test_check_index_health_active_snapshot(async_sqlite_session, tmp_path) -> None:
    import numpy as np

    from app.embedding.fake import FakeEmbeddingAdapter
    from app.index.faiss_index import FaissIndex
    from app.index.snapshot import build_manifest, save_manifest

    # Build a FAISS index
    adapter = FakeEmbeddingAdapter(dimension=8)
    idx = FaissIndex.create(8)
    vecs = adapter.embed_texts(["hello world"]).vectors
    ids = np.array([0], dtype=np.int64)
    idx.add(vecs, ids)
    faiss_path = tmp_path / "index.faiss"
    idx.save(faiss_path)

    # Build manifest
    doc_id = uuid.uuid4()
    ver_id = uuid.uuid4()
    manifest = build_manifest(
        manifest_embedding=adapter.manifest,
        faiss_path=faiss_path,
        document_versions={str(doc_id): str(ver_id)},
        document_count=1,
        chunk_count=1,
        max_faiss_id=0,
    )
    manifest_path = tmp_path / "manifest.json"
    save_manifest(manifest, manifest_path)

    # Create DB rows
    doc = Document(
        id=doc_id,
        filename="p.pdf",
        stored_filename="s.pdf",
        media_type="application/pdf",
        extension="pdf",
        sha256="a" * 64,
        file_size=128,
        status=DocumentStatus.READY,
        active_document_version_id=ver_id,
    )
    ver = DocumentVersion(
        id=ver_id,
        document_id=doc_id,
        status=DocumentVersionStatus.READY,
        chunk_config={},
        embedding_signature=adapter.manifest.signature,
        embedding_dimension=8,
        chunk_count=1,
    )
    snapshot = IndexSnapshot(
        id=uuid.uuid4(),
        status=IndexSnapshotStatus.ACTIVE,
        embedding_signature=adapter.manifest.signature,
        faiss_path=str(faiss_path),
        manifest_sha256=manifest.sha256,
        manifest=manifest.to_dict(),
        document_count=1,
        chunk_count=1,
        max_faiss_id=0,
    )
    sys_state = SystemState(id=1, active_index_snapshot_id=snapshot.id)
    async_sqlite_session.add_all([doc, ver, snapshot, sys_state])
    await async_sqlite_session.flush()

    result = await check_index_health(async_sqlite_session)
    assert result.healthy is True
    assert result.snapshot_id is not None
    assert result.chunk_count == 1


@pytest.mark.asyncio
async def test_check_index_health_file_missing(async_sqlite_session) -> None:
    snapshot = IndexSnapshot(
        id=uuid.uuid4(),
        status=IndexSnapshotStatus.ACTIVE,
        embedding_signature="sig",
        faiss_path="/nonexistent/path.faiss",
        manifest={},
        document_count=0,
        chunk_count=0,
    )
    sys_state = SystemState(id=1, active_index_snapshot_id=snapshot.id)
    async_sqlite_session.add_all([snapshot, sys_state])
    await async_sqlite_session.flush()

    result = await check_index_health(async_sqlite_session)
    assert result.healthy is False
    assert result.error_code == "FAISS_FILE_MISSING"


@pytest.mark.asyncio
async def test_check_index_health_snapshot_not_active(async_sqlite_session) -> None:
    snapshot = IndexSnapshot(
        id=uuid.uuid4(),
        status=IndexSnapshotStatus.SUPERSEDED,
        embedding_signature="sig",
        faiss_path="/tmp/idx.faiss",
        manifest={},
    )
    sys_state = SystemState(id=1, active_index_snapshot_id=snapshot.id)
    async_sqlite_session.add_all([snapshot, sys_state])
    await async_sqlite_session.flush()

    result = await check_index_health(async_sqlite_session)
    assert result.healthy is False
    assert result.error_code == "SNAPSHOT_NOT_ACTIVE"


@pytest.mark.asyncio
async def test_reconcile_stale_jobs(async_sqlite_session) -> None:
    job1 = IngestionJob(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        kind=JobKind.INGEST,
        status=JobStatus.RUNNING,
        stage=JobStage.EMBEDDING,
        attempt=1,
    )
    job2 = IngestionJob(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        kind=JobKind.INGEST,
        status=JobStatus.QUEUED,
        stage=JobStage.QUEUED,
        attempt=1,
    )
    async_sqlite_session.add_all([job1, job2])
    await async_sqlite_session.flush()

    count = await reconcile_stale_jobs(async_sqlite_session)
    assert count == 1
    await async_sqlite_session.refresh(job1)
    assert job1.status == JobStatus.FAILED
    assert job1.error_code == "STALE_ON_RESTART"

    await async_sqlite_session.refresh(job2)
    assert job2.status == JobStatus.QUEUED


@pytest.mark.asyncio
async def test_check_document_consistency_ok(async_sqlite_session) -> None:
    doc = _make_doc(async_sqlite_session)
    ver = DocumentVersion(
        id=uuid.uuid4(),
        document_id=doc.id,
        status=DocumentVersionStatus.READY,
        chunk_config={},
    )
    doc.active_document_version_id = ver.id
    async_sqlite_session.add(ver)
    await async_sqlite_session.flush()

    result = await check_document_consistency(async_sqlite_session)
    assert result == []


@pytest.mark.asyncio
async def test_check_document_consistency_bad_version(async_sqlite_session) -> None:
    doc = _make_doc(async_sqlite_session)
    ver = DocumentVersion(
        id=uuid.uuid4(),
        document_id=doc.id,
        status=DocumentVersionStatus.FAILED,
        chunk_config={},
    )
    doc.active_document_version_id = ver.id
    async_sqlite_session.add(ver)
    await async_sqlite_session.flush()

    result = await check_document_consistency(async_sqlite_session)
    assert len(result) == 1

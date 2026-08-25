"""End-to-end ingestion tests with embedding + FAISS indexing (spec §Phase 5).

Uses FakeEmbeddingAdapter (deterministic, no model download) and SQLite
session. Validates that the pipeline produces an active IndexSnapshot with
a valid FAISS index whose search results are consistent after reload.
"""

from __future__ import annotations

import uuid
from typing import Any

import numpy as np
import pytest
from sqlalchemy import select

from app.embedding.fake import FakeEmbeddingAdapter
from app.index.faiss_index import FaissIndex
from app.models.chunk import Chunk, DocumentVersion
from app.models.document import Document
from app.models.enums import (
    DocumentStatus,
    IndexSnapshotStatus,
    JobKind,
    JobStage,
    JobStatus,
)
from app.models.index_snapshot import IndexSnapshot, SystemState
from app.models.job import IngestionJob
from app.schemas.search import SearchRequest, SearchScope
from app.services.ingestion import run_ingest
from app.services.retrieval import search_corpus


class _FakeParser:
    """Returns fixed page/char counts."""

    def parse(self, document: Document) -> tuple[int, int]:
        return 3, 5000


class _RealChunker:
    """Creates deterministic Chunk ORM rows directly (bypasses chunking pipeline)."""

    def __init__(self, n_chunks: int = 5, session: Any = None) -> None:
        self._n = n_chunks
        self._session = session

    def chunk(self, document: Document, version: DocumentVersion) -> int:
        for i in range(self._n):
            c = Chunk(
                document_id=document.id,
                document_version_id=version.id,
                chunk_index=i,
                kind="text",
                section_path=[f"Section {i}"],
                raw_content=f"This is chunk {i} content for testing retrieval.",
                retrieval_content=f"Title Section {i} This is chunk {i} content for testing retrieval.",
                content_hash=f"hash_{i}",
                character_count=50,
            )
            self._session.add(c)
        return self._n


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
async def test_ingest_with_embedding_creates_snapshot(async_sqlite_session, tmp_path) -> None:
    doc, job = _make_doc_and_job()
    async_sqlite_session.add_all([doc, job])
    await async_sqlite_session.flush()

    adapter = FakeEmbeddingAdapter(dimension=32)
    await run_ingest(
        async_sqlite_session,
        job,
        doc,
        parser=_FakeParser(),
        chunker=_RealChunker(n_chunks=5, session=async_sqlite_session),
        embedding_provider=adapter,
        indexes_dir=tmp_path / "indexes",
    )
    await async_sqlite_session.commit()

    await async_sqlite_session.refresh(job)
    await async_sqlite_session.refresh(doc)
    assert job.status == JobStatus.SUCCEEDED
    assert doc.status == DocumentStatus.READY

    version = await async_sqlite_session.get(DocumentVersion, doc.active_document_version_id)
    assert version is not None
    assert version.embedding_signature == adapter.manifest.signature
    assert version.embedding_dimension == 32

    sys_state = await async_sqlite_session.get(SystemState, 1)
    assert sys_state is not None
    assert sys_state.active_index_snapshot_id is not None

    snapshot = await async_sqlite_session.get(IndexSnapshot, sys_state.active_index_snapshot_id)
    assert snapshot is not None
    assert snapshot.status == IndexSnapshotStatus.ACTIVE
    assert snapshot.chunk_count == 5
    assert snapshot.embedding_signature == adapter.manifest.signature

    chunks = (
        (
            await async_sqlite_session.execute(
                select(Chunk).where(Chunk.document_version_id == version.id)
            )
        )
        .scalars()
        .all()
    )
    assert all(c.faiss_id is not None for c in chunks)


@pytest.mark.asyncio
async def test_ingest_with_embedding_topk_consistency(async_sqlite_session, tmp_path) -> None:
    """Spec Phase 5 acceptance: restart (reload) gives same top-k."""
    doc, job = _make_doc_and_job()
    async_sqlite_session.add_all([doc, job])
    await async_sqlite_session.flush()

    adapter = FakeEmbeddingAdapter(dimension=32)
    await run_ingest(
        async_sqlite_session,
        job,
        doc,
        parser=_FakeParser(),
        chunker=_RealChunker(n_chunks=5, session=async_sqlite_session),
        embedding_provider=adapter,
        indexes_dir=tmp_path / "indexes",
    )
    await async_sqlite_session.commit()

    await async_sqlite_session.refresh(doc)

    sys_state = await async_sqlite_session.get(SystemState, 1)
    snapshot = await async_sqlite_session.get(IndexSnapshot, sys_state.active_index_snapshot_id)

    from pathlib import Path

    faiss_path = Path(snapshot.faiss_path)
    assert faiss_path.exists()

    idx = FaissIndex.load(faiss_path)
    query_result = adapter.embed_query("chunk 2 content testing")
    scores, result_ids = idx.search(query_result.vectors[0], top_k=3)

    # Reload and search again — must give identical results
    idx2 = FaissIndex.load(faiss_path)
    scores2, result_ids2 = idx2.search(query_result.vectors[0], top_k=3)

    np.testing.assert_array_equal(result_ids, result_ids2)
    np.testing.assert_allclose(scores, scores2)


@pytest.mark.asyncio
async def test_ingest_reindex_supersedes_old_snapshot(async_sqlite_session, tmp_path) -> None:
    """Reindex creates a new snapshot; old one is superseded."""
    doc, job = _make_doc_and_job()
    async_sqlite_session.add_all([doc, job])
    await async_sqlite_session.flush()

    adapter = FakeEmbeddingAdapter(dimension=32)
    await run_ingest(
        async_sqlite_session,
        job,
        doc,
        parser=_FakeParser(),
        chunker=_RealChunker(n_chunks=3, session=async_sqlite_session),
        embedding_provider=adapter,
        indexes_dir=tmp_path / "indexes",
    )
    await async_sqlite_session.commit()

    sys_state = await async_sqlite_session.get(SystemState, 1)
    old_snapshot_id = sys_state.active_index_snapshot_id

    # Reingest
    job2 = IngestionJob(
        id=uuid.uuid4(),
        document_id=doc.id,
        kind=JobKind.REINDEX,
        status=JobStatus.QUEUED,
        stage=JobStage.QUEUED,
        attempt=1,
    )
    async_sqlite_session.add(job2)
    await async_sqlite_session.flush()

    await run_ingest(
        async_sqlite_session,
        job2,
        doc,
        parser=_FakeParser(),
        chunker=_RealChunker(n_chunks=5, session=async_sqlite_session),
        embedding_provider=adapter,
        indexes_dir=tmp_path / "indexes2",
    )
    await async_sqlite_session.commit()

    await async_sqlite_session.refresh(sys_state)
    assert sys_state.active_index_snapshot_id != old_snapshot_id

    old = await async_sqlite_session.get(IndexSnapshot, old_snapshot_id)
    assert old.status == IndexSnapshotStatus.SUPERSEDED

    new = await async_sqlite_session.get(IndexSnapshot, sys_state.active_index_snapshot_id)
    assert new.status == IndexSnapshotStatus.ACTIVE


@pytest.mark.asyncio
async def test_ingest_without_embedding_skips_indexing(async_sqlite_session) -> None:
    """When embedding_provider is None, pipeline still completes but no snapshot."""
    doc, job = _make_doc_and_job()
    async_sqlite_session.add_all([doc, job])
    await async_sqlite_session.flush()

    await run_ingest(
        async_sqlite_session,
        job,
        doc,
        parser=_FakeParser(),
        chunker=_RealChunker(n_chunks=3, session=async_sqlite_session),
    )
    await async_sqlite_session.commit()

    await async_sqlite_session.refresh(job)
    await async_sqlite_session.refresh(doc)
    assert job.status == JobStatus.SUCCEEDED
    assert doc.status == DocumentStatus.READY

    sys_state = await async_sqlite_session.get(SystemState, 1)
    assert sys_state is None or sys_state.active_index_snapshot_id is None


@pytest.mark.asyncio
async def test_second_ingest_snapshot_keeps_first_document(async_sqlite_session, tmp_path) -> None:
    adapter = FakeEmbeddingAdapter(dimension=32)
    first_doc, first_job = _make_doc_and_job()
    second_doc, second_job = _make_doc_and_job()
    second_doc.sha256 = "b" * 64
    second_doc.stored_filename = "second.pdf"
    async_sqlite_session.add_all([first_doc, first_job, second_doc, second_job])
    await async_sqlite_session.flush()

    for doc, job in ((first_doc, first_job), (second_doc, second_job)):
        await run_ingest(
            async_sqlite_session,
            job,
            doc,
            parser=_FakeParser(),
            chunker=_RealChunker(n_chunks=2, session=async_sqlite_session),
            embedding_provider=adapter,
            indexes_dir=tmp_path / "indexes",
        )
        await async_sqlite_session.commit()

    state = await async_sqlite_session.get(SystemState, 1)
    snapshot = await async_sqlite_session.get(IndexSnapshot, state.active_index_snapshot_id)
    assert snapshot.document_count == 2
    assert snapshot.chunk_count == 4

    response = await search_corpus(
        async_sqlite_session,
        SearchRequest(
            query="testing retrieval",
            scope=SearchScope(type="documents", document_ids=[first_doc.id]),
            top_k=2,
        ),
        adapter,
    )
    assert response.results
    assert {result.document_id for result in response.results} == {first_doc.id}

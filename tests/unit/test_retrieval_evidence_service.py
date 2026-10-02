from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.embedding.fake import FakeEmbeddingAdapter
from app.models.chunk import Chunk
from app.models.collection import Collection, CollectionDocument
from app.rerank.base import RerankError
from app.schemas.search import SearchRequest, SearchScope
from app.services.ingestion import run_ingest
from app.services.retrieval import as_evidence_candidate, search_corpus
from tests.unit.test_ingestion_embedding import _FakeParser, _make_doc_and_job, _RealChunker


async def corpus(session, tmp_path):
    adapter = FakeEmbeddingAdapter(dimension=32)
    docs = []
    for i in range(2):
        doc, job = _make_doc_and_job()
        doc.sha256 = str(i) * 64
        doc.stored_filename = f"{i}.pdf"
        session.add_all([doc, job])
        await session.flush()
        await run_ingest(
            session,
            job,
            doc,
            parser=_FakeParser(),
            chunker=_RealChunker(n_chunks=3, session=session),
            embedding_provider=adapter,
            indexes_dir=tmp_path / "indexes",
        )
        await session.commit()
        docs.append(doc)
    return docs, adapter


@pytest.mark.parametrize("strategy", ["legacy", "cell_coverage"])
@pytest.mark.parametrize("scope_type", ["all", "documents", "collection"])
async def test_selection_wiring_scope_and_debug(
    async_sqlite_session, tmp_path, strategy, scope_type
):
    session = async_sqlite_session
    docs, adapter = await corpus(session, tmp_path)
    if scope_type == "documents":
        scope = SearchScope(type="documents", document_ids=[docs[0].id])
    elif scope_type == "collection":
        collection = Collection(id=uuid.uuid4(), name="test")
        session.add(collection)
        await session.flush()
        session.add(CollectionDocument(collection_id=collection.id, document_id=docs[0].id))
        await session.commit()
        scope = SearchScope(type="collection", collection_id=collection.id)
    else:
        scope = SearchScope(type="all")
    response = await search_corpus(
        session,
        SearchRequest(query="testing retrieval", scope=scope, top_k=10, debug=True),
        adapter,
        settings=get_settings().model_copy(
            update={
                "retrieval_selection": strategy,
                "retrieval_dense_top_k": 100,
                "retrieval_bm25_top_k": 100,
                "retrieval_rrf_top_k": 80,
            }
        ),
    )
    assert response.results
    allowed = {doc.id for doc in docs} if scope_type == "all" else {docs[0].id}
    assert {r.document_id for r in response.results} <= allowed
    debug = response.debug
    assert debug["policy"]["rrf_top_k"] == 80
    assert debug["policy"]["selection"] == strategy
    assert debug["policy"]["document_balance"] == "explicit_scope"
    assert len(debug["rerank"]) == (6 if scope_type == "all" else 3)
    assert len(debug["selected"]) == len(response.results)
    assert all(value >= 0 for value in debug["timings_ms"].values())
    if scope_type == "all":
        assert len(response.results) == (6 if strategy == "cell_coverage" else 3)
    assert all(r.context_content == r.raw_content for r in response.results)
    mapped = debug["candidate_chunk_ids"]
    for stage in ("dense", "bm25", "rrf", "rerank", "selected"):
        assert all(str(row[0]) in mapped for row in debug[stage])


async def test_explicit_document_balance_keeps_each_scoped_document(async_sqlite_session, tmp_path):
    docs, adapter = await corpus(async_sqlite_session, tmp_path)
    response = await search_corpus(
        async_sqlite_session,
        SearchRequest(
            query="testing",
            scope=SearchScope(type="documents", document_ids=[doc.id for doc in docs]),
            top_k=2,
            debug=True,
        ),
        adapter,
        settings=get_settings().model_copy(update={"retrieval_document_balance": "explicit_scope"}),
    )
    assert {result.document_id for result in response.results} == {doc.id for doc in docs}


@pytest.mark.parametrize("failure", ["unavailable", "invalid_scores", "programming_bug"])
async def test_only_expected_rerank_failures_degrade(
    async_sqlite_session, tmp_path, monkeypatch, failure
):
    docs, adapter = await corpus(async_sqlite_session, tmp_path)

    class Reranker:
        def rerank(self, query, passages):
            if failure == "invalid_scores":
                return [float("nan")] * len(passages)
            if failure == "unavailable":
                raise RerankError("test model unavailable")
            raise ValueError("unexpected programming bug")

    monkeypatch.setattr("app.services.retrieval.get_reranker", lambda _settings: Reranker())
    request = SearchRequest(
        query="testing", scope=SearchScope(type="documents", document_ids=[docs[0].id]), debug=True
    )
    if failure == "programming_bug":
        with pytest.raises(ValueError, match="programming bug"):
            await search_corpus(async_sqlite_session, request, adapter)
    else:
        response = await search_corpus(async_sqlite_session, request, adapter)
        assert response.degraded_reasons == ["RERANK_UNAVAILABLE"]
        assert response.debug["rerank"] == response.debug["rrf"]


@pytest.mark.parametrize(
    ("subtype", "values", "expected"),
    [
        ("table_raw_text", [str(uuid.UUID(int=1))], 0),
        ("table_row", [str(uuid.UUID(int=1))], 1),
        ("table_group", ["not-a-uuid"], 0),
        ("table_row", None, 0),
    ],
)
def test_metadata_boundary_does_not_guess_cells(subtype, values, expected):
    chunk = Chunk(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_version_id=uuid.uuid4(),
        faiss_id=1,
        kind="table",
        content_hash="h",
        parent_chunk_id=uuid.uuid4(),
        metadata_={"chunk_subtype": subtype, "cell_ids": values},
    )
    assert len(as_evidence_candidate(chunk).cell_ids) == expected


async def test_real_table_metadata_suppresses_only_covered_child(async_sqlite_session, tmp_path):
    session = async_sqlite_session
    docs, adapter = await corpus(session, tmp_path)
    chunks = list(
        (
            await session.execute(
                select(Chunk).where(Chunk.document_id == docs[0].id).order_by(Chunk.faiss_id)
            )
        ).scalars()
    )
    element_id, cell_id = str(uuid.uuid4()), str(uuid.uuid4())
    for chunk in chunks[:2]:
        chunk.kind = "table"
        chunk.metadata_ = {
            "element_id": element_id,
            "chunk_subtype": "table_group",
            "cell_ids": [cell_id],
        }
    await session.commit()
    response = await search_corpus(
        session,
        SearchRequest(
            query="testing",
            scope=SearchScope(type="documents", document_ids=[docs[0].id]),
            debug=True,
        ),
        adapter,
        settings=get_settings().model_copy(update={"retrieval_selection": "cell_coverage"}),
    )
    assert len(response.results) == 2
    assert any(d["reason"] == "cells_covered" for d in response.debug["selection_decisions"])
    ids = {r.chunk_id for r in response.results}
    assert all(
        uuid.UUID(owner) in ids
        for d in response.debug["selection_decisions"]
        for owner in d["covered_by"]
    )


def test_search_openapi_remains_compatible(client):
    schemas = client.get("/api/openapi.json").json()["components"]["schemas"]
    request = schemas["SearchRequest"]["properties"]
    assert request["top_k"]["maximum"] == 20
    assert request["top_k"]["default"] == 8
    assert "debug" in schemas["SearchResponse"]["properties"]
    assert "retrieval_queries" in schemas["SearchResponse"]["properties"]
    chat = schemas["ChatResponse"]
    assert "retrieval_queries" in chat["required"]
    assert "retrieval_workflow" in chat["required"]

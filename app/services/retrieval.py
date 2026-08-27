from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import DependencyUnavailableError, IndexUnavailableError, NotFoundError
from app.embedding.base import EmbeddingProvider
from app.index.faiss_index import FaissIndex
from app.models.chunk import Chunk
from app.models.collection import CollectionDocument
from app.models.document import Document
from app.models.enums import DocumentStatus
from app.models.index_snapshot import IndexSnapshot, SystemState
from app.rerank import get_reranker
from app.retrieval.bm25 import BM25Index
from app.retrieval.fusion import rrf_fuse
from app.retrieval.table import TableContextChunk, expand_table_context
from app.schemas.search import SearchRequest, SearchResponse, SearchResultOut


async def search_corpus(
    session: AsyncSession,
    request: SearchRequest,
    embedding_provider: EmbeddingProvider,
    *,
    original_query: str | None = None,
) -> SearchResponse:
    original_query = original_query or request.query
    state = await session.get(SystemState, 1)
    snapshot = (
        await session.get(IndexSnapshot, state.active_index_snapshot_id)
        if state is not None and state.active_index_snapshot_id is not None
        else None
    )
    if snapshot is None or not snapshot.faiss_path or not snapshot.bm25_path:
        raise IndexUnavailableError(message="Index is unavailable.")
    if snapshot.embedding_signature != embedding_provider.manifest.signature:
        raise DependencyUnavailableError(
            code="INDEX_INCOMPATIBLE",
            message="Configured embedding model does not match the active index.",
        )

    document_ids = await _resolve_scope(session, request)
    chunk_rows = list(
        (
            await session.execute(
                select(Chunk, Document)
                .join(Document, Chunk.document_id == Document.id)
                .where(
                    Chunk.faiss_id.is_not(None),
                    Chunk.document_id.in_(document_ids),
                    Chunk.document_version_id == Document.active_document_version_id,
                    Document.status == DocumentStatus.READY,
                )
            )
        ).all()
    )
    by_faiss = {int(chunk.faiss_id): (chunk, doc) for chunk, doc in chunk_rows}
    allowed = set(by_faiss)
    if not allowed:
        return SearchResponse(
            original_query=original_query,
            rewritten_query=request.query,
            results=[],
            degraded_reasons=["EMPTY_SCOPE"],
        )

    query_vector = embedding_provider.embed_query(request.query).vectors[0]
    faiss = FaissIndex.load(
        Path(snapshot.faiss_path), expected_dimension=embedding_provider.manifest.dimension
    )
    scores, ids = faiss.search(query_vector, top_k=faiss.ntotal)
    dense = [
        (int(fid), float(score))
        for score, fid in zip(scores, ids, strict=True)
        if int(fid) in allowed
    ][:30]

    bm25 = BM25Index.from_dict(json.loads(Path(snapshot.bm25_path).read_text(encoding="utf-8")))
    sparse_query = (
        request.query if original_query == request.query else f"{original_query}\n{request.query}"
    )
    sparse = bm25.search(
        sparse_query,
        top_k=30,
        scope_doc_ids=allowed,
        minimum_should_match=request.minimum_should_match,
    )
    fused = rrf_fuse(dense, sparse, top_k=max(30, request.top_k))
    degraded_reasons: list[str] = []
    ranked = fused
    try:
        reranker = get_reranker()
        passages = [by_faiss[faiss_id][0].retrieval_content for faiss_id, _, _ in fused]
        rerank_scores = reranker.rerank(request.query, passages)
        ranked = [
            (faiss_id, float(rerank_score), "rerank")
            for (faiss_id, _score, _source), rerank_score in zip(fused, rerank_scores, strict=True)
        ]
        ranked.sort(key=lambda item: (-item[1], item[0]))
    except Exception:
        degraded_reasons.append("RERANK_UNAVAILABLE")
    active_version_ids = {
        document.active_document_version_id
        for _chunk, document in chunk_rows
        if document.active_document_version_id is not None
    }
    table_chunks = list(
        (
            await session.execute(
                select(Chunk).where(
                    Chunk.document_version_id.in_(active_version_ids), Chunk.kind == "table"
                )
            )
        ).scalars()
    )
    table_context_chunks = [_as_table_context(chunk) for chunk in table_chunks]
    table_context_by_id = {chunk.chunk_id: chunk for chunk in table_context_chunks}

    unique_ranked: list[tuple[int, float, str]] = []
    seen_chunk_ids: set[uuid.UUID] = set()
    seen_hashes: set[str] = set()
    for item in ranked:
        chunk = by_faiss[item[0]][0]
        if chunk.id in seen_chunk_ids or chunk.content_hash in seen_hashes:
            continue
        seen_chunk_ids.add(chunk.id)
        seen_hashes.add(chunk.content_hash)
        unique_ranked.append(item)

    results: list[SearchResultOut] = []
    expansions: dict[str, list[str]] = {}
    for rank, (faiss_id, score, _source) in enumerate(unique_ranked[: request.top_k], start=1):
        chunk, document = by_faiss[faiss_id]
        context_content = chunk.raw_content
        expanded_chunk_ids = [chunk.id]
        table_hit = table_context_by_id.get(chunk.id)
        if table_hit is not None:
            expanded = expand_table_context(table_hit, table_context_chunks, request.query)
            context_content = expanded.content
            expanded_chunk_ids = expanded.chunk_ids
            expansions[str(chunk.id)] = [str(chunk_id) for chunk_id in expanded.chunk_ids]
        metadata = chunk.metadata_ or {}
        element_kind = _citation_element_kind(metadata)
        results.append(
            SearchResultOut(
                chunk_id=chunk.id,
                document_id=document.id,
                document_title=document.title or document.filename,
                section_path=chunk.section_path,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                raw_content=chunk.raw_content,
                context_content=context_content,
                expanded_chunk_ids=expanded_chunk_ids,
                element_id=metadata.get("element_id"),
                element_kind=element_kind,
                cell_ids=metadata.get("cell_ids") or [],
                bboxes=metadata.get("bboxes") or [],
                score=score,
                rank=rank,
            )
        )
    debug = None
    if request.debug:
        debug = {
            "dense": dense,
            "bm25": sparse,
            "rrf": fused,
            "table_expansions": expansions,
        }
    return SearchResponse(
        original_query=original_query,
        rewritten_query=request.query,
        results=results,
        degraded_reasons=degraded_reasons,
        debug=debug,
    )


def _citation_element_kind(metadata: dict[str, object]) -> str | None:
    element_kind = metadata.get("element_kind")
    if metadata.get("chunk_subtype") == "table_raw_text" and not metadata.get("cell_ids"):
        return "table_raw_text"
    return str(element_kind) if element_kind is not None else None


def _as_table_context(chunk: Chunk) -> TableContextChunk:
    metadata = chunk.metadata_ or {}
    return TableContextChunk(
        chunk_id=chunk.id,
        chunk_index=chunk.chunk_index,
        raw_content=chunk.raw_content,
        retrieval_content=chunk.retrieval_content,
        content_hash=chunk.content_hash,
        parent_chunk_id=chunk.parent_chunk_id,
        subtype=str(metadata.get("chunk_subtype") or ""),
        column_header_paths=[
            [str(part) for part in path]
            for path in (metadata.get("column_header_paths") or [])
            if isinstance(path, list)
        ],
    )


async def _resolve_scope(session: AsyncSession, request: SearchRequest) -> set[uuid.UUID]:
    scope = request.scope
    if scope.type == "documents":
        requested = set(scope.document_ids)
        found = set(
            (
                await session.execute(
                    select(Document.id).where(
                        Document.id.in_(requested), Document.status == DocumentStatus.READY
                    )
                )
            ).scalars()
        )
        if found != requested:
            raise NotFoundError(
                code="DOCUMENT_NOT_FOUND",
                message="A scoped document is missing or not ready.",
            )
        return found
    if scope.type == "collection":
        return set(
            (
                await session.execute(
                    select(CollectionDocument.document_id)
                    .join(Document, CollectionDocument.document_id == Document.id)
                    .where(
                        CollectionDocument.collection_id == scope.collection_id,
                        Document.status == DocumentStatus.READY,
                    )
                )
            ).scalars()
        )
    return set(
        (
            await session.execute(
                select(Document.id).where(Document.status == DocumentStatus.READY)
            )
        ).scalars()
    )

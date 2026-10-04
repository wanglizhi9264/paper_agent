from __future__ import annotations

import json
import uuid
from pathlib import Path
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import DependencyUnavailableError, IndexUnavailableError, NotFoundError
from app.core.config import Settings, get_settings
from app.embedding.base import EmbeddingProvider
from app.index.faiss_index import FaissIndex
from app.models.chunk import Chunk
from app.models.collection import CollectionDocument
from app.models.document import Document
from app.models.enums import DocumentStatus
from app.models.index_snapshot import IndexSnapshot, SystemState
from app.rerank import RerankError, get_reranker
from app.retrieval.bm25 import BM25Index
from app.retrieval.evidence import EvidenceCandidate, rank_candidates, select_evidence
from app.retrieval.fusion import rrf_fuse
from app.retrieval.table import TableContextChunk, expand_table_context
from app.schemas.search import SearchRequest, SearchResponse, SearchResultOut


async def search_corpus(
    session: AsyncSession,
    request: SearchRequest,
    embedding_provider: EmbeddingProvider,
    *,
    original_query: str | None = None,
    settings: Settings | None = None,
) -> SearchResponse:
    settings = settings or get_settings()
    started = perf_counter()
    timings: dict[str, float] = {}
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
            retrieval_queries=[request.query],
        )
    balance_document_ids = _balanced_document_ids(request, document_ids, settings)

    timings["scope"] = (perf_counter() - started) * 1000
    stage_started = perf_counter()
    query_vector = embedding_provider.embed_query(request.query).vectors[0]
    timings["embedding"] = (perf_counter() - stage_started) * 1000
    stage_started = perf_counter()
    faiss = FaissIndex.load(
        Path(snapshot.faiss_path), expected_dimension=embedding_provider.manifest.dimension
    )
    scores, ids = faiss.search(query_vector, top_k=faiss.ntotal)
    dense_all = [
        (int(fid), float(score))
        for score, fid in zip(scores, ids, strict=True)
        if int(fid) in allowed
    ]
    dense = _ensure_document_candidates(
        dense_all,
        settings.retrieval_dense_top_k,
        balance_document_ids,
        by_faiss,
    )
    timings["dense"] = (perf_counter() - stage_started) * 1000
    stage_started = perf_counter()

    bm25 = BM25Index.from_dict(json.loads(Path(snapshot.bm25_path).read_text(encoding="utf-8")))
    sparse_query = (
        request.query if original_query == request.query else f"{original_query}\n{request.query}"
    )
    sparse_all = bm25.search(
        sparse_query,
        top_k=len(allowed) if balance_document_ids else settings.retrieval_bm25_top_k,
        scope_doc_ids=allowed,
        minimum_should_match=request.minimum_should_match,
    )
    sparse = _ensure_document_candidates(
        sparse_all,
        settings.retrieval_bm25_top_k,
        balance_document_ids,
        by_faiss,
    )
    timings["bm25"] = (perf_counter() - stage_started) * 1000
    fusion_top_k = settings.retrieval_rrf_top_k
    if balance_document_ids:
        fusion_top_k = max(fusion_top_k, len({row[0] for row in dense + sparse}))
    fused = rrf_fuse(dense, sparse, top_k=fusion_top_k)
    degraded_reasons: list[str] = []
    ranked = fused
    stage_started = perf_counter()
    try:
        reranker = get_reranker(settings)
        passages = [by_faiss[faiss_id][0].retrieval_content for faiss_id, _, _ in fused]
        rerank_scores = reranker.rerank(request.query, passages)
        ranked = rank_candidates(fused, rerank_scores)
    except RerankError:
        degraded_reasons.append("RERANK_UNAVAILABLE")
    timings["rerank"] = (perf_counter() - stage_started) * 1000
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

    stage_started = perf_counter()
    selection = select_evidence(
        ranked,
        {fid: as_evidence_candidate(chunk) for fid, (chunk, _doc) in by_faiss.items()},
        top_k=request.top_k,
        strategy=settings.retrieval_selection,
        balance_document_ids=balance_document_ids,
    )
    timings["selection"] = (perf_counter() - stage_started) * 1000

    results: list[SearchResultOut] = []
    expansions: dict[str, list[str]] = {}
    for rank, (faiss_id, score, _source) in enumerate(selection.selected, start=1):
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
            "snapshot_id": str(snapshot.id),
            "policy": {
                "dense_top_k": settings.retrieval_dense_top_k,
                "bm25_top_k": settings.retrieval_bm25_top_k,
                "rrf_top_k": settings.retrieval_rrf_top_k,
                "selection": settings.retrieval_selection,
                "document_balance": settings.retrieval_document_balance,
            },
            "rerank": ranked,
            "selected": selection.selected,
            "selection_decisions": [
                {
                    "chunk_id": str(d.chunk_id),
                    "reason": d.reason,
                    "covered_by": [str(cid) for cid in d.covered_by],
                }
                for d in selection.decisions
            ],
            "candidate_chunk_ids": {
                str(fid): str(by_faiss[fid][0].id)
                for fid in sorted(
                    {
                        *(row[0] for row in dense),
                        *(row[0] for row in sparse),
                        *(row[0] for row in fused),
                        *(row[0] for row in ranked),
                        *(row[0] for row in selection.selected),
                    }
                )
            },
            "timings_ms": {**timings, "total": (perf_counter() - started) * 1000},
        }
    return SearchResponse(
        original_query=original_query,
        rewritten_query=request.query,
        results=results,
        degraded_reasons=degraded_reasons,
        retrieval_queries=[request.query],
        debug=debug,
    )


def as_evidence_candidate(chunk: Chunk) -> EvidenceCandidate:
    """Map persisted provenance, never infer cell equivalence from text/row numbers."""
    metadata = chunk.metadata_ or {}
    table_id = None
    if chunk.kind == "table":
        table_id = str(chunk.parent_chunk_id or metadata.get("element_id") or "") or None
    cell_ids: frozenset[uuid.UUID] = frozenset()
    values = metadata.get("cell_ids")
    if metadata.get("chunk_subtype") in {"table_row", "table_group"} and isinstance(values, list):
        try:
            cell_ids = frozenset(uuid.UUID(str(value)) for value in values)
        except ValueError:
            # Legacy/malformed provenance cannot justify suppressing evidence.
            cell_ids = frozenset()
    if chunk.faiss_id is None:
        raise ValueError("Evidence candidate must be indexed")
    return EvidenceCandidate(
        faiss_id=chunk.faiss_id,
        chunk_id=chunk.id,
        document_id=chunk.document_id,
        document_version_id=chunk.document_version_id,
        content_hash=chunk.content_hash,
        table_id=table_id,
        cell_ids=cell_ids,
    )


def _balanced_document_ids(
    request: SearchRequest,
    resolved_document_ids: set[uuid.UUID],
    settings: Settings,
) -> frozenset[uuid.UUID]:
    if (
        settings.retrieval_document_balance != "explicit_scope"
        or request.scope.type != "documents"
        or len(resolved_document_ids) <= 1
        or len(resolved_document_ids) > request.top_k
    ):
        return frozenset()
    return frozenset(resolved_document_ids)


def _ensure_document_candidates(
    ranked: list[tuple[int, float]],
    top_k: int,
    balance_document_ids: frozenset[uuid.UUID],
    by_faiss: dict[int, tuple[Chunk, Document]],
) -> list[tuple[int, float]]:
    selected = ranked[:top_k]
    if not balance_document_ids:
        return selected
    covered = {by_faiss[row[0]][0].document_id for row in selected}
    for row in ranked[top_k:]:
        document_id = by_faiss[row[0]][0].document_id
        if document_id in balance_document_ids and document_id not in covered:
            selected.append(row)
            covered.add(document_id)
        if balance_document_ids.issubset(covered):
            break
    return selected


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

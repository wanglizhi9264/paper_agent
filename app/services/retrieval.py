from __future__ import annotations

import json
import uuid
from pathlib import Path
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import DependencyUnavailableError, IndexUnavailableError, NotFoundError
from app.core.config import Settings, get_settings
from app.core.gpu import acquire_gpu_slot
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
from app.retrieval.fusion import (
    build_rerank_candidate_pool,
    build_source_aware_candidate_pool,
    merge_ranked_views_best_rank,
    rrf_fuse,
    rrf_fuse_many,
)
from app.retrieval.table import TableContextChunk, expand_table_context
from app.schemas.search import SearchRequest, SearchResponse, SearchResultOut
from app.services.retrieval_queries import RetrievalQueries


async def search_corpus(
    session: AsyncSession,
    request: SearchRequest,
    embedding_provider: EmbeddingProvider,
    *,
    original_query: str | None = None,
    settings: Settings | None = None,
    retrieval_queries: RetrievalQueries | None = None,
    translation_latency_ms: float = 0.0,
    pinned_snapshot_id: uuid.UUID | None = None,
    pinned_versions: dict[uuid.UUID, uuid.UUID] | None = None,
) -> SearchResponse:
    settings = settings or get_settings()
    queue_started = perf_counter()
    await acquire_gpu_slot(session)
    gpu_wait_ms = (perf_counter() - queue_started) * 1000
    started = perf_counter()
    timings: dict[str, float] = {}
    original_query = original_query or request.query
    query_plan = retrieval_queries or RetrievalQueries(original=request.query)
    if query_plan.original != request.query:
        raise ValueError("RetrievalQueries.original must match SearchRequest.query")
    state = await session.get(SystemState, 1)
    snapshot = (
        await session.get(IndexSnapshot, pinned_snapshot_id or state.active_index_snapshot_id)
        if state is not None and state.active_index_snapshot_id is not None
        else None
    )
    if snapshot is None or not snapshot.faiss_path or not snapshot.bm25_path:
        raise IndexUnavailableError(message="Index is unavailable.")
    timings["snapshot"] = (perf_counter() - started) * 1000
    stage_started = perf_counter()
    embedding_manifest = embedding_provider.manifest
    timings["embedding_model_ready"] = (perf_counter() - stage_started) * 1000
    if snapshot.embedding_signature != embedding_manifest.signature:
        raise DependencyUnavailableError(
            code="INDEX_INCOMPATIBLE",
            message="Configured embedding model does not match the active index.",
        )

    scope_started = perf_counter()
    document_ids = await _resolve_scope(session, request)
    if pinned_versions is not None:
        document_ids &= set(pinned_versions)
    chunk_rows = list(
        (
            await session.execute(
                select(Chunk, Document)
                .join(Document, Chunk.document_id == Document.id)
                .where(
                    Chunk.faiss_id.is_not(None),
                    Chunk.document_id.in_(document_ids),
                    (
                        Chunk.document_version_id.in_(list(pinned_versions.values()))
                        if pinned_versions is not None
                        else Chunk.document_version_id == Document.active_document_version_id
                    ),
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
            retrieval_queries=query_plan.executed(),
        )
    balance_document_ids = _balanced_document_ids(request, document_ids, settings)

    timings["scope_db"] = (perf_counter() - scope_started) * 1000
    timings["scope"] = (perf_counter() - started) * 1000
    stage_started = perf_counter()
    faiss = FaissIndex.load(
        Path(snapshot.faiss_path), expected_dimension=embedding_manifest.dimension
    )
    faiss_load_ms = (perf_counter() - stage_started) * 1000
    stage_started = perf_counter()
    bm25 = BM25Index.from_dict(json.loads(Path(snapshot.bm25_path).read_text(encoding="utf-8")))
    bm25_load_ms = (perf_counter() - stage_started) * 1000
    timings["faiss_index_load"] = faiss_load_ms
    timings["bm25_index_load"] = bm25_load_ms
    dense_lists: dict[str, list[tuple[int, float]]] = {}
    sparse_lists: dict[str, list[tuple[int, float]]] = {}
    named_queries = [("original", query_plan.original)]
    if query_plan.english is not None:
        named_queries.append(("english", query_plan.english))

    sparse_queries: dict[str, str] = {}
    for name, query in named_queries:
        stage_started = perf_counter()
        query_vector = embedding_provider.embed_query(query).vectors[0]
        timings[f"embedding_{name}"] = (perf_counter() - stage_started) * 1000
        stage_started = perf_counter()
        scores, ids = faiss.search(query_vector, top_k=faiss.ntotal)
        dense_all = [
            (int(fid), float(score))
            for score, fid in zip(scores, ids, strict=True)
            if int(fid) in allowed
        ]
        dense_lists[name] = _ensure_document_candidates(
            dense_all,
            settings.retrieval_dense_top_k,
            balance_document_ids,
            by_faiss,
        )
        timings[f"dense_{name}"] = (perf_counter() - stage_started) * 1000
        if name == "original":
            timings[f"dense_{name}"] += faiss_load_ms

        stage_started = perf_counter()
        sparse_query = query
        if query_plan.english is None and original_query != request.query:
            sparse_query = f"{original_query}\n{request.query}"
        sparse_queries[name] = sparse_query
        sparse_all = bm25.search(
            sparse_query,
            top_k=len(allowed) if balance_document_ids else settings.retrieval_bm25_top_k,
            scope_doc_ids=allowed,
            minimum_should_match=request.minimum_should_match,
        )
        sparse_lists[name] = _ensure_document_candidates(
            sparse_all,
            settings.retrieval_bm25_top_k,
            balance_document_ids,
            by_faiss,
        )
        timings[f"bm25_{name}"] = (perf_counter() - stage_started) * 1000
        if name == "original":
            timings[f"bm25_{name}"] += bm25_load_ms

    dense = dense_lists["original"]
    sparse = sparse_lists["original"]
    dual_enabled = query_plan.english is not None
    dense_merged = (
        merge_ranked_views_best_rank([dense_lists["original"], dense_lists["english"]])
        if dual_enabled
        else dense
    )
    bm25_merged = (
        merge_ranked_views_best_rank([sparse_lists["original"], sparse_lists["english"]])
        if dual_enabled
        else sparse
    )
    if query_plan.english is None:
        timings["embedding"] = timings["embedding_original"]
        timings["dense"] = timings["dense_original"]
        timings["bm25"] = timings["bm25_original"]
    fusion_top_k = settings.retrieval_rrf_top_k
    if balance_document_ids:
        fusion_top_k = max(
            fusion_top_k,
            len(
                {
                    row[0]
                    for results in (*dense_lists.values(), *sparse_lists.values())
                    for row in results
                }
            ),
        )
    stage_started = perf_counter()
    if not dual_enabled:
        fused = rrf_fuse(dense, sparse, top_k=fusion_top_k)
    elif settings.retrieval_cross_language_fusion == "retriever_best_rank_rrf":
        fused = rrf_fuse(dense_merged, bm25_merged, top_k=fusion_top_k)
    else:
        fused = rrf_fuse_many(
            [
                ("dense_original", dense_lists["original"]),
                ("dense_english", dense_lists["english"]),
                ("bm25_original", sparse_lists["original"]),
                ("bm25_english", sparse_lists["english"]),
            ],
            top_k=fusion_top_k,
        )
    timings["fusion"] = (perf_counter() - stage_started) * 1000
    stage_started = perf_counter()
    if dual_enabled:
        rerank_pool, candidate_origins = build_rerank_candidate_pool(
            fused,
            dense_merged,
            bm25_merged,
            fused_depth=settings.retrieval_rerank_top_k,
            rescue_strategy=settings.retrieval_candidate_rescue,
            rescue_top_m=settings.retrieval_candidate_rescue_top_m,
        )
    else:
        rerank_pool = list(fused)
        candidate_origins = {row[0]: "rrf" for row in rerank_pool}
    timings["rescue"] = (perf_counter() - stage_started) * 1000
    rescued_pool = list(rerank_pool)
    rescue_origins = dict(candidate_origins)
    stage_started = perf_counter()
    if settings.retrieval_candidate_promotion == "source_top_m":
        promotion_sources = [
            ("dense_original", dense_lists["original"]),
            ("bm25_original", sparse_lists["original"]),
        ]
        if dual_enabled:
            promotion_sources = [
                ("dense_original", dense_lists["original"]),
                ("dense_english", dense_lists["english"]),
                ("bm25_original", sparse_lists["original"]),
                ("bm25_english", sparse_lists["english"]),
            ]
        rerank_pool, candidate_origins = build_source_aware_candidate_pool(
            rerank_pool,
            promotion_sources,
            total_depth=settings.retrieval_rerank_top_k,
            per_source_top_m=settings.retrieval_candidate_promotion_top_m,
        )
    timings["promotion"] = (perf_counter() - stage_started) * 1000
    if query_plan.translation_attempted:
        timings["translation"] = translation_latency_ms
    degraded_reasons: list[str] = []
    ranked = rerank_pool
    rerank_query = (
        query_plan.english or request.query
        if dual_enabled and settings.retrieval_rerank_query == "english_if_available"
        else request.query
    )
    stage_started = perf_counter()
    try:
        reranker = get_reranker(settings)
        passages = [by_faiss[faiss_id][0].retrieval_content for faiss_id, _, _ in rerank_pool]
        rerank_scores = reranker.rerank(rerank_query, passages)
        ranked = rank_candidates(rerank_pool, rerank_scores)
    except RerankError:
        degraded_reasons.append("RERANK_UNAVAILABLE")
    timings["rerank"] = (perf_counter() - stage_started) * 1000
    active_version_ids = {chunk.document_version_id for chunk, _document in chunk_rows}
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
    debug: dict[str, object] | None = None
    if request.debug:
        debug = {
            "schema_version": 1,
            "embedding_manifest": embedding_manifest.to_dict(),
            "index": {
                "type": "IndexIDMap2/FlatIP",
                "dimension": faiss.dimension,
                "total_vectors": faiss.ntotal,
                "scoped_vectors": len(allowed),
                "scope_strategy": "scan_all_then_scope_before_top_k",
            },
            "bm25_explanations": {
                name: bm25.explain(query, [row[0] for row in sparse_lists[name]])
                for name, query in sparse_queries.items()
            },
            "rrf_k": 60,
            "minimum_should_match": request.minimum_should_match,
            "fusion_lists": (
                {
                    "dense_original": dense_lists["original"],
                    "dense_english": dense_lists.get("english", []),
                    "bm25_original": sparse_lists["original"],
                    "bm25_english": sparse_lists.get("english", []),
                }
                if dual_enabled and settings.retrieval_cross_language_fusion == "four_way_rrf"
                else {"dense": dense_merged, "bm25": bm25_merged}
            ),
            "reranker": {
                "model_id": settings.rerank_model,
                "revision": settings.rerank_revision,
                "max_tokens": settings.rerank_max_tokens,
                "status": "unavailable" if degraded_reasons else "applied",
                "input_content": "retrieval_content",
            },
            "degraded_reasons": degraded_reasons,
            "gpu_wait_ms": gpu_wait_ms,
            "dense": dense,
            "bm25": sparse,
            "dense_original": dense_lists["original"],
            "dense_english": dense_lists.get("english", []),
            "bm25_original": sparse_lists["original"],
            "bm25_english": sparse_lists.get("english", []),
            "dense_merged": dense_merged if dual_enabled else None,
            "bm25_merged": bm25_merged if dual_enabled else None,
            "rrf": fused,
            "fused": fused,
            "fusion_strategy": (
                settings.retrieval_cross_language_fusion
                if dual_enabled
                else "standard_two_source_rrf"
            ),
            "rescue_strategy": (settings.retrieval_candidate_rescue if dual_enabled else "off"),
            "rescued_candidates": [
                {"faiss_id": row[0], "source": rescue_origins[row[0]]} for row in rescued_pool
            ],
            "promoted_candidates": [
                {"faiss_id": row[0], "source": candidate_origins[row[0]]} for row in rerank_pool
            ],
            "promotion_strategy": (settings.retrieval_candidate_promotion),
            "promotion_top_m": (
                settings.retrieval_candidate_promotion_top_m
                if settings.retrieval_candidate_promotion != "off"
                else None
            ),
            "table_expansions": expansions,
            "snapshot_id": str(snapshot.id),
            "policy": {
                "dense_top_k": settings.retrieval_dense_top_k,
                "bm25_top_k": settings.retrieval_bm25_top_k,
                "rrf_top_k": settings.retrieval_rrf_top_k,
                "rerank_top_k": settings.retrieval_rerank_top_k,
                "candidate_promotion": (settings.retrieval_candidate_promotion),
                "selection": settings.retrieval_selection,
                "document_balance": settings.retrieval_document_balance,
                "cross_language_retrieval": query_plan.mode,
                "cross_language_translation_attempted": query_plan.translation_attempted,
            },
            "retrieval_queries": {
                "original": query_plan.original,
                "english": query_plan.english,
            },
            "rerank_query_strategy": (
                settings.retrieval_rerank_query if dual_enabled else "original"
            ),
            "rerank_query": rerank_query,
            "reranker_query": rerank_query,
            "rerank_input_count": len(rerank_pool),
            "rerank_input": rerank_pool,
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
                        *(row[0] for row in dense_lists.get("english", [])),
                        *(row[0] for row in sparse_lists.get("english", [])),
                        *(row[0] for row in dense_merged),
                        *(row[0] for row in bm25_merged),
                        *(row[0] for row in fused),
                        *(row[0] for row in rerank_pool),
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
        retrieval_queries=query_plan.executed(),
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

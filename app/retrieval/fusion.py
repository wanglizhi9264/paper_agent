from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

RankedPair = tuple[int, float]
RankedCandidate = tuple[int, float, str]


@dataclass
class RetrievalResult:
    """Unified retrieval result (spec §14.3, §14.4)."""

    chunk_id: str
    faiss_id: int
    score: float
    source: str  # "dense" | "bm25" | "rrf" | "rerank" | "expanded"
    rank: int = 0
    document_id: str = ""
    section_path: list[str] | None = None
    raw_content: str = ""
    retrieval_content: str = ""
    page_start: int | None = None
    page_end: int | None = None
    metadata: dict[str, Any] | None = None
    expanded_from_chunk_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "faiss_id": self.faiss_id,
            "score": self.score,
            "source": self.source,
            "rank": self.rank,
            "document_id": self.document_id,
            "section_path": self.section_path,
            "raw_content": self.raw_content,
            "page_start": self.page_start,
            "page_end": self.page_end,
        }


def rrf_fuse(
    dense_results: list[tuple[int, float]],
    bm25_results: list[tuple[int, float]],
    *,
    k: int = 60,
    top_k: int = 30,
) -> list[tuple[int, float, str]]:
    """Reciprocal Rank Fusion (spec §14.3).

    RRF: sum(1 / (k + rank)), rank from 1.
    Returns [(faiss_id, rrf_score, best_source), ...] sorted by RRF desc,
    then best source rank asc, then faiss_id asc.
    """
    return rrf_fuse_many(
        [("dense", dense_results), ("bm25", bm25_results)],
        k=k,
        top_k=top_k,
    )


def rrf_fuse_many(
    ranked_lists: Sequence[tuple[str, Sequence[tuple[int, float]]]],
    *,
    k: int = 60,
    top_k: int = 30,
) -> list[tuple[int, float, str]]:
    """Fuse any number of named ranked lists using deterministic equal-weight RRF.

    A repeated id contributes at most once per list and once for every distinct
    retrieval view. Ties use best rank and then FAISS id, preserving legacy RRF.
    """
    if k < 0:
        raise ValueError("RRF k must be non-negative")
    if top_k < 0:
        raise ValueError("RRF top_k must be non-negative")

    scores: dict[int, float] = {}
    best_rank: dict[int, int] = {}
    best_source_order: dict[int, int] = {}
    best_source: dict[int, str] = {}
    for source_order, (source, results) in enumerate(ranked_lists):
        seen_in_source: set[int] = set()
        for rank, (faiss_id, _score) in enumerate(results, start=1):
            if faiss_id in seen_in_source:
                continue
            seen_in_source.add(faiss_id)
            scores[faiss_id] = scores.get(faiss_id, 0.0) + 1.0 / (k + rank)
            previous = best_rank.get(faiss_id)
            if previous is None or rank < previous:
                best_rank[faiss_id] = rank
                best_source_order[faiss_id] = source_order
                best_source[faiss_id] = source
            elif rank == previous and source_order < best_source_order[faiss_id]:
                best_source_order[faiss_id] = source_order
                best_source[faiss_id] = source

    result = sorted(
        scores.items(),
        key=lambda item: (
            -item[1],
            best_rank[item[0]],
            item[0],
        ),
    )
    return [(faiss_id, score, best_source[faiss_id]) for faiss_id, score in result[:top_k]]


def merge_ranked_views_best_rank(
    ranked_views: Sequence[Sequence[RankedPair]],
) -> list[RankedPair]:
    """Merge query views of one retriever by each document's best 1-based rank.

    Scores are carried from the winning occurrence for diagnostics only. Ordering
    is best rank, declaration order of the winning view, then FAISS id.
    """
    best: dict[int, tuple[int, int, float]] = {}
    for view_order, results in enumerate(ranked_views):
        seen: set[int] = set()
        for rank, (faiss_id, score) in enumerate(results, start=1):
            if faiss_id in seen:
                continue
            seen.add(faiss_id)
            candidate = (rank, view_order, score)
            previous = best.get(faiss_id)
            if previous is None or candidate[:2] < previous[:2]:
                best[faiss_id] = candidate
    ordered = sorted(best, key=lambda faiss_id: (*best[faiss_id][:2], faiss_id))
    return [(faiss_id, best[faiss_id][2]) for faiss_id in ordered]


def build_rerank_candidate_pool(
    fused: Sequence[RankedCandidate],
    dense_merged: Sequence[RankedPair],
    bm25_merged: Sequence[RankedPair],
    *,
    fused_depth: int,
    rescue_strategy: str = "off",
    rescue_top_m: int = 10,
) -> tuple[list[RankedCandidate], dict[int, str]]:
    """Build a deterministic reranker pool and audit each candidate's origin."""
    if fused_depth < 0 or rescue_top_m < 0:
        raise ValueError("Candidate depths must be non-negative")
    if rescue_strategy not in {"off", "retriever_top_m"}:
        raise ValueError("Invalid candidate rescue strategy")

    pool = list(fused[:fused_depth])
    origins: dict[int, set[str]] = {row[0]: {"rrf"} for row in pool}
    by_id = {row[0]: row for row in pool}
    if rescue_strategy == "retriever_top_m":
        for source, rows in (
            ("dense_rescue", dense_merged[:rescue_top_m]),
            ("bm25_rescue", bm25_merged[:rescue_top_m]),
        ):
            for faiss_id, score in rows:
                origins.setdefault(faiss_id, set()).add(source)
                if faiss_id not in by_id:
                    candidate = (faiss_id, score, source)
                    by_id[faiss_id] = candidate
                    pool.append(candidate)

    labels = {
        faiss_id: next(iter(values)) if len(values) == 1 else "multiple"
        for faiss_id, values in origins.items()
    }
    return pool, labels


def build_source_aware_candidate_pool(
    main_candidates: Sequence[RankedCandidate],
    source_lists: Sequence[tuple[str, Sequence[RankedPair]]],
    *,
    total_depth: int,
    per_source_top_m: int,
) -> tuple[list[RankedCandidate], dict[int, str]]:
    """Reserve source-specific candidates, then fill a strict-size reranker pool.

    Membership is selected source-first so every declared source receives its quota.
    Returned order preserves the main candidate order, followed by promoted candidates
    in source declaration/rank order. Scores are diagnostic only before reranking.
    """
    if total_depth < 0 or per_source_top_m < 0:
        raise ValueError("Candidate depths must be non-negative")

    origins: dict[int, set[str]] = {}
    reserved_rows: dict[int, RankedCandidate] = {}
    reserved_order: list[int] = []
    for source, rows in source_lists:
        seen_in_source: set[int] = set()
        for faiss_id, score in rows[:per_source_top_m]:
            if faiss_id in seen_in_source:
                continue
            seen_in_source.add(faiss_id)
            origins.setdefault(faiss_id, set()).add(source)
            if faiss_id not in reserved_rows:
                reserved_rows[faiss_id] = (faiss_id, score, source)
                reserved_order.append(faiss_id)

    selected_ids = set(reserved_order[:total_depth])
    for faiss_id, _score, _source in main_candidates:
        if len(selected_ids) >= total_depth:
            break
        selected_ids.add(faiss_id)

    pool: list[RankedCandidate] = []
    emitted: set[int] = set()
    for row in main_candidates:
        faiss_id = row[0]
        if faiss_id in selected_ids and faiss_id not in emitted:
            pool.append(row)
            emitted.add(faiss_id)
            origins.setdefault(faiss_id, set()).add("main")
    for faiss_id in reserved_order:
        if faiss_id in selected_ids and faiss_id not in emitted:
            pool.append(reserved_rows[faiss_id])
            emitted.add(faiss_id)

    labels = {
        faiss_id: next(iter(values)) if len(values) == 1 else "multiple"
        for faiss_id, values in origins.items()
        if faiss_id in emitted
    }
    return pool, labels

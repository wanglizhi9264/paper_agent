from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


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

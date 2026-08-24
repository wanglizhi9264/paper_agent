from __future__ import annotations

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
    scores: dict[int, float] = {}
    best_rank: dict[int, int] = {}
    best_source: dict[int, str] = {}

    for rank, (faiss_id, _score) in enumerate(dense_results, start=1):
        rrf_score = 1.0 / (k + rank)
        scores[faiss_id] = scores.get(faiss_id, 0.0) + rrf_score
        best_rank[faiss_id] = rank
        best_source[faiss_id] = "dense"

    for rank, (faiss_id, _score) in enumerate(bm25_results, start=1):
        rrf_score = 1.0 / (k + rank)
        scores[faiss_id] = scores.get(faiss_id, 0.0) + rrf_score
        if faiss_id not in best_rank or rank < best_rank[faiss_id]:
            best_rank[faiss_id] = rank
            best_source[faiss_id] = "bm25"

    # Sort: RRF desc, best source rank asc, faiss_id asc.
    result = sorted(
        scores.items(),
        key=lambda x: (-x[1], best_rank[x[0]], x[0]),
    )
    return [(faiss_id, score, best_source[faiss_id]) for faiss_id, score in result[:top_k]]

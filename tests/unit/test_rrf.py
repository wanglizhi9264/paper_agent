from __future__ import annotations

from app.retrieval.fusion import rrf_fuse


def test_rrf_basic() -> None:
    dense = [(1, 0.9), (2, 0.8), (3, 0.7)]
    bm25 = [(2, 5.0), (4, 3.0), (1, 2.0)]
    results = rrf_fuse(dense, bm25, k=60, top_k=10)
    # Both lists contain IDs 1 and 2 — they should rank top
    top_ids = [r[0] for r in results]
    assert 1 in top_ids[:2]
    assert 2 in top_ids[:2]


def test_rrf_score_formula() -> None:
    """RRF score = sum(1/(k+rank)), rank from 1 (spec §14.3)."""
    dense = [(1, 0.9)]
    bm25 = [(1, 5.0)]
    results = rrf_fuse(dense, bm25, k=60, top_k=10)
    expected = 1.0 / (60 + 1) + 1.0 / (60 + 1)
    assert abs(results[0][1] - expected) < 1e-10


def test_rrf_rank_from_1() -> None:
    """Rank starts from 1, not 0 (spec §14.3)."""
    dense = [(10, 0.9)]
    results = rrf_fuse(dense, [], k=60, top_k=10)
    assert abs(results[0][1] - 1.0 / (60 + 1)) < 1e-10


def test_rrf_tie_break_by_best_rank() -> None:
    """Tie-break: best source rank asc, then chunk_id asc (spec §14.3)."""
    dense = [(1, 0.9), (2, 0.8)]  # rank 1, 2
    bm25 = [(2, 5.0), (1, 3.0)]  # rank 1, 2
    results = rrf_fuse(dense, bm25, k=60, top_k=10)
    # ID 1 has best_rank=1 (dense rank 1), ID 2 has best_rank=1 (bm25 rank 1)
    # Same RRF score (both at rank 1+rank 2), so tie-break by best_rank
    # Both have best_rank=1, so tie-break by faiss_id asc → 1 before 2
    assert results[0][0] == 1


def test_rrf_tie_break_by_chunk_id() -> None:
    """When RRF score and best_rank are equal, sort by chunk_id asc."""
    # Both at rank 1 in their respective lists → equal RRF and best_rank
    dense = [(5, 0.9), (3, 0.9)]
    bm25 = [(3, 5.0), (5, 5.0)]
    results = rrf_fuse(dense, bm25, k=60, top_k=10)
    # ID 3 and 5 both at rank 1+1 → same RRF, same best_rank
    # Tie-break by faiss_id asc → 3 before 5
    assert results[0][0] == 3


def test_rrf_top_k_limit() -> None:
    dense = [(i, 1.0 - i * 0.1) for i in range(10)]
    bm25 = []
    results = rrf_fuse(dense, bm25, k=60, top_k=5)
    assert len(results) == 5


def test_rrf_no_overlap() -> None:
    dense = [(1, 0.9), (2, 0.8)]
    bm25 = [(3, 5.0), (4, 3.0)]
    results = rrf_fuse(dense, bm25, k=60, top_k=10)
    assert len(results) == 4
    all_ids = {r[0] for r in results}
    assert all_ids == {1, 2, 3, 4}


def test_rrf_empty_inputs() -> None:
    results = rrf_fuse([], [], k=60, top_k=10)
    assert results == []


def test_rrf_best_source() -> None:
    dense = [(1, 0.9)]
    bm25 = [(2, 5.0)]
    results = rrf_fuse(dense, bm25, k=60, top_k=10)
    sources = {r[0]: r[2] for r in results}
    assert sources[1] == "dense"
    assert sources[2] == "bm25"

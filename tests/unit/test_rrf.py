from __future__ import annotations

from app.retrieval.fusion import (
    build_rerank_candidate_pool,
    build_source_aware_candidate_pool,
    merge_ranked_views_best_rank,
    rrf_fuse,
    rrf_fuse_many,
)


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


def test_rrf_many_accumulates_four_independent_lists() -> None:
    results = rrf_fuse_many(
        [
            ("dense_original", [(1, 0.9), (2, 0.8)]),
            ("dense_english", [(2, 0.9), (1, 0.8)]),
            ("bm25_original", [(3, 5.0), (1, 4.0)]),
            ("bm25_english", [(1, 5.0), (3, 4.0)]),
        ],
        k=60,
        top_k=10,
    )

    assert results[0][0] == 1
    expected = 2 / 61 + 2 / 62
    assert abs(results[0][1] - expected) < 1e-10


def test_rrf_many_duplicate_id_contributes_once_per_list() -> None:
    results = rrf_fuse_many(
        [("dense_original", [(1, 0.9), (1, 0.8)]), ("bm25_original", [(1, 5.0)])],
        k=60,
        top_k=10,
    )

    assert results == [(1, 2 / 61, "dense_original")]


def test_rrf_many_is_deterministic_when_scores_and_ranks_tie() -> None:
    ranked_lists = [
        ("dense_original", [(5, 1.0), (3, 0.9)]),
        ("dense_english", [(3, 1.0), (5, 0.9)]),
    ]

    first = rrf_fuse_many(ranked_lists, top_k=10)
    second = rrf_fuse_many(ranked_lists, top_k=10)

    assert first == second
    assert [row[0] for row in first] == [3, 5]


def test_legacy_rrf_delegates_without_changing_results() -> None:
    dense = [(7, 0.9), (2, 0.8)]
    bm25 = [(2, 5.0), (9, 4.0)]

    assert rrf_fuse(dense, bm25, k=60, top_k=10) == rrf_fuse_many(
        [("dense", dense), ("bm25", bm25)],
        k=60,
        top_k=10,
    )


def test_best_rank_merge_uses_best_view_and_keeps_single_view_items() -> None:
    merged = merge_ranked_views_best_rank(
        [[(1, 0.9), (2, 0.8), (3, 0.7)], [(3, 0.95), (4, 0.6), (1, 0.5)]]
    )

    assert [row[0] for row in merged] == [1, 3, 2, 4]
    assert dict(merged)[3] == 0.95
    assert 2 in dict(merged)
    assert 4 in dict(merged)


def test_best_rank_merge_tie_is_view_order_then_faiss_id() -> None:
    views = [[(5, 1.0), (8, 0.8)], [(3, 1.0), (8, 0.9)]]

    assert merge_ranked_views_best_rank(views) == [
        (5, 1.0),
        (3, 1.0),
        (8, 0.8),
    ]


def test_candidate_rescue_recovers_strong_dense_item_and_deduplicates() -> None:
    pool, origins = build_rerank_candidate_pool(
        [(1, 0.1, "rrf"), (2, 0.09, "rrf")],
        [(3, 0.9), (1, 0.8)],
        [(4, 5.0), (3, 4.0)],
        fused_depth=2,
        rescue_strategy="retriever_top_m",
        rescue_top_m=2,
    )

    assert [row[0] for row in pool] == [1, 2, 3, 4]
    assert origins == {1: "multiple", 2: "rrf", 3: "multiple", 4: "bm25_rescue"}


def test_candidate_rescue_off_preserves_fused_depth() -> None:
    fused = [(1, 0.1, "rrf"), (2, 0.09, "rrf"), (3, 0.08, "rrf")]

    pool, origins = build_rerank_candidate_pool(
        fused,
        [(4, 0.9)],
        [(5, 5.0)],
        fused_depth=2,
    )

    assert pool == fused[:2]
    assert origins == {1: "rrf", 2: "rrf"}


def test_source_aware_pool_reserves_each_source_and_stays_at_depth() -> None:
    main = [(index, 1.0 / index, "rrf") for index in range(1, 11)]
    pool, origins = build_source_aware_candidate_pool(
        main,
        [
            ("dense_original", [(20, 0.9), (1, 0.8)]),
            ("dense_english", [(21, 0.9), (2, 0.8)]),
            ("bm25_original", [(22, 9.0), (3, 8.0)]),
            ("bm25_english", [(23, 9.0), (4, 8.0)]),
        ],
        total_depth=8,
        per_source_top_m=1,
    )

    assert len(pool) == 8
    assert {row[0] for row in pool} == {1, 2, 3, 4, 20, 21, 22, 23}
    assert origins[20] == "dense_original"
    assert origins[1] == "main"


def test_source_aware_pool_is_deterministic_and_deduplicates_views() -> None:
    args = (
        [(1, 0.5, "rrf"), (2, 0.4, "rrf"), (3, 0.3, "rrf")],
        [("dense_original", [(4, 0.9), (1, 0.8)]), ("dense_english", [(4, 0.7)])],
    )

    first = build_source_aware_candidate_pool(*args, total_depth=3, per_source_top_m=1)
    second = build_source_aware_candidate_pool(*args, total_depth=3, per_source_top_m=1)

    assert first == second
    assert [row[0] for row in first[0]] == [1, 2, 4]
    assert first[1][4] == "multiple"

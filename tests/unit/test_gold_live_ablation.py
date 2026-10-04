from __future__ import annotations

import pytest

from eval.gold_live_ablation import _stage_chunk_ids


def test_stage_chunk_ids_maps_faiss_ids_without_using_gold() -> None:
    debug = {
        "dense": [[10, 0.9], [20, 0.8]],
        "candidate_chunk_ids": {"10": "chunk-a", "20": "chunk-b"},
    }
    assert _stage_chunk_ids(debug, "dense_only") == ["chunk-a", "chunk-b"]


def test_stage_chunk_ids_fails_when_a_top_ten_id_is_unmapped() -> None:
    debug = {
        "bm25": [[10, 3.0], [20, 2.0]],
        "candidate_chunk_ids": {"10": "chunk-a"},
    }
    with pytest.raises(ValueError, match="unmapped"):
        _stage_chunk_ids(debug, "bm25_only")


def test_stage_chunk_ids_supports_final_selected_stage() -> None:
    debug = {
        "selected": [[20, 0.8, "rerank"], [10, 0.7, "rerank"]],
        "candidate_chunk_ids": {"10": "chunk-a", "20": "chunk-b"},
    }

    assert _stage_chunk_ids(debug, "selected") == ["chunk-b", "chunk-a"]

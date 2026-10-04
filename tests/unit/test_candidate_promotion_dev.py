from __future__ import annotations

from eval.candidate_promotion_dev import analyze_depth_losses
from eval.ranking_pipeline_ab import TRACE_STAGES


def test_depth_loss_analysis_counts_best_source_without_guessing_missing_ranks() -> None:
    ranks = {stage: {"gold": None} for stage in TRACE_STAGES}
    ranks["dense_english"] = {"gold": 12}
    ranks["bm25_english"] = {"gold": 4}
    ranks["fused"] = {"gold": 40}
    rows = [
        {
            "id": "q1",
            "failure_category": "LOST_BEFORE_RERANK_DEPTH",
            "gold_ranks": ranks,
        },
        {"id": "q2", "failure_category": "SURVIVED_TOP10", "gold_ranks": ranks},
    ]

    result = analyze_depth_losses(rows)

    assert result["count"] == 1
    assert result["best_source_counts"] == {"bm25_english": 1}
    assert result["cases"][0]["best_rank"] == 4
    assert result["cases"][0]["fused_gold_rank"] == 40

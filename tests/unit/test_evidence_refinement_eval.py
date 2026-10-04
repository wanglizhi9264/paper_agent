from __future__ import annotations

import pytest

from eval.evidence_refinement import WORKFLOWS, score_retrieval, summarize


def test_refinement_eval_has_locked_before_after_variants() -> None:
    assert WORKFLOWS == ("single_pass", "bounded_refinement", "routed_multi_search")


def test_refinement_eval_uses_strict_chunk_id_metrics() -> None:
    assert score_retrieval({"a", "b"}, ["alias-a", "a"]) == {
        "recall@10": 0.5,
        "mrr": 0.5,
    }
    with pytest.raises(ValueError):
        score_retrieval(set(), [])


def test_refinement_eval_reports_cost_and_refinement_rate() -> None:
    result = summarize(
        [
            {"recall@10": 0.5, "mrr": 1.0, "retrieval_calls": 1},
            {"recall@10": 1.0, "mrr": 0.5, "retrieval_calls": 2},
        ]
    )

    assert result == {
        "n": 2,
        "recall@10": 0.75,
        "mrr": 0.75,
        "mean_retrieval_calls": 1.5,
        "refinement_rate": 0.5,
        "multi_search_rate": 0.5,
    }

from __future__ import annotations

import pytest

from eval.evidence_selection import VARIANTS, retrieval_query, score_row


def test_query_excludes_oracle_and_assistant_content():
    assert (
        retrieval_query(
            {
                "question": "current",
                "expected_standalone_query": "oracle",
                "reference_answer": "secret answer",
                "conversation": [
                    {"role": "assistant", "content": "oracle"},
                    {"role": "user", "content": "prior"},
                ],
            }
        )
        == "prior\ncurrent"
    )


def test_metrics_are_strict_chunk_id_not_alias_credit():
    assert score_row({"a", "b"}, ["alias-of-a", "a"], {"a", "b"}) == {
        "recall@10": 0.5,
        "mrr": 0.5,
        "candidate_recall": 1.0,
    }
    with pytest.raises(ValueError):
        score_row(set(), [], set())


def test_variants_fix_pool_and_selector_independently():
    assert VARIANTS == {
        "legacy30": (30, 30, 30, "legacy"),
        "wide80": (100, 100, 80, "legacy"),
        "wide80_coverage": (100, 100, 80, "cell_coverage"),
    }

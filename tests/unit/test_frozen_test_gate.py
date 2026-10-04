from __future__ import annotations

from eval.frozen_test_gate import citation_diagnostics


def _item(case_id: str = "q1") -> dict:
    return {
        "id": case_id,
        "split": "test",
        "answerable": True,
        "snapshot_labels": {
            "evidence_groups": [{"status": "resolved", "resolved_chunk_ids": ["gold"]}]
        },
    }


def test_citation_diagnostics_separates_retrieval_and_citation_losses() -> None:
    items = [_item("miss"), _item("citation-miss"), _item("hit")]
    predictions = [
        {
            "id": "miss",
            "retrieved_chunk_ids": ["other"],
            "predicted_citation_chunk_ids": [],
        },
        {
            "id": "citation-miss",
            "retrieved_chunk_ids": ["gold", "other"],
            "predicted_citation_chunk_ids": ["other"],
        },
        {
            "id": "hit",
            "retrieved_chunk_ids": ["gold"],
            "predicted_citation_chunk_ids": ["gold"],
        },
    ]

    result = citation_diagnostics(items, predictions)

    assert result["counts"] == {
        "retrieval_miss": 1,
        "retrieval_hit_citation_miss": 1,
        "retrieval_hit_citation_hit": 1,
    }
    detail = result["retrieval_hit_citation_miss_samples"][0]
    assert detail["question_id"] == "citation-miss"
    assert detail["gold_evidence_rank"] == {"gold": 1}
    assert detail["generated_citations"] == ["other"]
    assert detail["missed_gold_chunks"] == ["gold"]

from __future__ import annotations

from copy import deepcopy

from eval.cross_language_retrieval import (
    STAGES,
    decide,
    dev_answerable_items,
    summarize_variant,
)


def _item(case_id: str, chunk_id: str, *, split: str = "dev") -> dict:
    return {
        "id": case_id,
        "answerable": True,
        "split": split,
        "snapshot_labels": {
            "evidence_groups": [
                {
                    "status": "resolved",
                    "resolved_chunk_ids": [chunk_id],
                }
            ]
        },
    }


def _row(case_id: str, gold: str, *, rank: int | None) -> dict:
    ranked = [f"other-{index}" for index in range(100)]
    if rank is not None:
        ranked[rank - 1] = gold
    stage_ids = {stage: list(ranked) for stage in STAGES}
    return {
        "id": case_id,
        "degraded_reasons": [],
        "stage_chunk_ids": stage_ids,
        "gold_ranks": {stage: {gold: rank} for stage in STAGES},
        "timings_ms": {"dense_original": 2.0, "total": 9.0},
    }


def test_dev_selector_never_returns_test_or_unanswerable_rows() -> None:
    payload = {
        "dataset": [
            _item("dev", "gold-dev"),
            _item("test", "gold-test", split="test"),
            {**_item("negative", "unused"), "answerable": False},
        ]
    }

    assert [row["id"] for row in dev_answerable_items(payload)] == ["dev"]


def test_variant_summary_reports_deep_candidate_and_gold_ranks() -> None:
    items = [_item("one", "gold-1"), _item("two", "gold-2")]
    rows = [_row("one", "gold-1", rank=42), _row("two", "gold-2", rank=None)]

    summary = summarize_variant(items, rows)

    assert summary["diagnostic_stages"]["dense_original"]["recall@30"] == 0.0
    assert summary["diagnostic_stages"]["dense_original"]["recall@50"] == 0.5
    assert summary["diagnostic_stages"]["dense_original"]["gold_rank"] == {
        "mean": 42.0,
        "median": 42,
        "observed": 1,
        "missing": 1,
    }
    assert summary["candidate_union_recall"]["per_list@50"] == 0.5
    assert summary["candidate_pool_miss_ids"]["per_list@50"] == ["two"]


def test_decision_requires_final_rerank_improvement() -> None:
    baseline = {
        "production_stages": {"rerank": {"recall@10": 0.4, "mrr": 0.3}},
        "candidate_union_recall": {"per_list@50": 0.5},
        "translation_failure_rate": 0.0,
    }
    dual = deepcopy(baseline)
    dual["production_stages"]["rerank"]["recall@10"] = 0.45
    dual["production_stages"]["rerank"]["mrr"] = 0.295
    dual["candidate_union_recall"]["per_list@50"] = 0.6

    assert decide({"off": baseline, "cjk_to_english_dual": dual})["decision"] == "KEEP OPT-IN"

    dual["production_stages"]["rerank"]["recall@10"] = 0.4
    assert decide({"off": baseline, "cjk_to_english_dual": dual})["decision"] == "REJECT"

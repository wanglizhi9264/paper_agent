from __future__ import annotations

from eval.ranking_pipeline_ab import TRACE_STAGES, _winner, classify_failure


def _item() -> dict:
    return {
        "snapshot_labels": {
            "evidence_groups": [{"status": "resolved", "resolved_chunk_ids": ["gold"]}]
        }
    }


def _traces() -> dict[str, list[str]]:
    return {stage: ["gold"] for stage in TRACE_STAGES}


def test_failure_classifier_uses_last_loss_stage() -> None:
    traces = _traces()
    assert classify_failure(_item(), traces) == "SURVIVED_TOP10"

    traces["rerank"] = ["other"]
    assert classify_failure(_item(), traces) == "LOST_IN_RERANK"

    traces["rerank_input"] = ["other"]
    assert classify_failure(_item(), traces) == "LOST_BEFORE_RERANK_DEPTH"

    traces["fused"] = ["other"]
    assert classify_failure(_item(), traces) == "LOST_IN_FUSION"

    for stage in ("dense_original", "dense_english", "bm25_original", "bm25_english"):
        traces[stage] = ["other"]
    assert classify_failure(_item(), traces) == "NOT_IN_RAW_UNION"


def test_winner_prefers_recall_then_mrr_then_latency() -> None:
    summaries = {
        "slow": {"recall@10": 0.5, "mrr": 0.4, "ndcg@10": 0.3, "mean_total_latency_ms": 20},
        "fast": {"recall@10": 0.5, "mrr": 0.4, "ndcg@10": 0.3, "mean_total_latency_ms": 10},
        "lower": {"recall@10": 0.4, "mrr": 0.9, "ndcg@10": 0.9, "mean_total_latency_ms": 1},
    }

    assert _winner(summaries, ["slow", "fast", "lower"]) == "fast"

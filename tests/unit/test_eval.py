"""Tests for retrieval evaluation metrics (spec §21)."""

from __future__ import annotations

import json
from pathlib import Path

from eval.retrieval_eval import (
    compute_citation_metrics,
    compute_mrr,
    compute_ndcg,
    compute_recall,
    evaluate_retrieval,
    load_dataset,
)


def test_compute_recall_perfect() -> None:
    retrieved = ["a", "b", "c"]
    relevant = ["a", "b"]
    assert compute_recall(retrieved, relevant, k=3) == 1.0


def test_compute_recall_partial() -> None:
    retrieved = ["a", "b", "c"]
    relevant = ["a", "d"]
    assert compute_recall(retrieved, relevant, k=3) == 0.5


def test_compute_recall_no_hit() -> None:
    retrieved = ["x", "y"]
    relevant = ["a"]
    assert compute_recall(retrieved, relevant, k=2) == 0.0


def test_compute_recall_k_cutoff() -> None:
    retrieved = ["x", "y", "a"]
    relevant = ["a"]
    # k=2 → top-2 is [x, y] → no hit
    assert compute_recall(retrieved, relevant, k=2) == 0.0
    # k=3 → top-3 includes "a"
    assert compute_recall(retrieved, relevant, k=3) == 1.0


def test_compute_mrr_first_position() -> None:
    assert compute_mrr(["a", "b"], ["a"]) == 1.0


def test_compute_mrr_second_position() -> None:
    assert compute_mrr(["x", "a"], ["a"]) == 0.5


def test_compute_mrr_no_hit() -> None:
    assert compute_mrr(["x", "y"], ["a"]) == 0.0


def test_compute_ndcg_perfect() -> None:
    retrieved = ["a", "b"]
    relevant = ["a", "b"]
    assert compute_ndcg(retrieved, relevant, k=5) == 1.0


def test_compute_ndcg_partial() -> None:
    retrieved = ["x", "a"]
    relevant = ["a"]
    # DCG = 1/log2(3) ≈ 0.6309
    # IDCG = 1/log2(2) = 1.0
    result = compute_ndcg(retrieved, relevant, k=5)
    assert 0.5 < result < 0.7


def test_compute_ndcg_no_hit() -> None:
    assert compute_ndcg(["x", "y"], ["a"], k=5) == 0.0


def test_evaluate_retrieval_aggregation() -> None:
    results = [
        {"retrieved_ids": ["a", "b"], "relevant_ids": ["a"]},
        {"retrieved_ids": ["c", "d"], "relevant_ids": ["d"]},
    ]
    metrics = evaluate_retrieval(results)
    assert 0 < metrics.recall_at_1 <= 1.0
    assert 0 < metrics.mrr <= 1.0


def test_evaluate_retrieval_empty() -> None:
    metrics = evaluate_retrieval([])
    assert metrics.recall_at_1 == 0.0
    assert metrics.mrr == 0.0


def test_citation_metrics_perfect() -> None:
    m = compute_citation_metrics(["a", "b"], ["a", "b"])
    assert m.precision == 1.0
    assert m.recall == 1.0


def test_citation_metrics_partial() -> None:
    m = compute_citation_metrics(["a", "x"], ["a", "b"])
    assert m.precision == 0.5
    assert m.recall == 0.5


def test_citation_metrics_no_predictions() -> None:
    m = compute_citation_metrics([], ["a"])
    assert m.precision == 0.0
    assert m.recall == 0.0


def test_load_dataset(tmp_path: Path) -> None:
    data = {"dataset": [{"id": "q1", "question": "test", "relevant_chunk_ids": ["c1"]}]}
    p = tmp_path / "dataset.json"
    p.write_text(json.dumps(data))
    loaded = load_dataset(p)
    assert len(loaded) == 1
    assert loaded[0]["question"] == "test"

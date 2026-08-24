"""Retrieval evaluation: Recall@K, MRR, nDCG@K (spec §21)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class RetrievalMetrics:
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    recall_at_10: float
    mrr: float
    ndcg_at_5: float
    ndcg_at_10: float

    def to_dict(self) -> dict[str, float]:
        return {
            "recall@1": self.recall_at_1,
            "recall@3": self.recall_at_3,
            "recall@5": self.recall_at_5,
            "recall@10": self.recall_at_10,
            "mrr": self.mrr,
            "ndcg@5": self.ndcg_at_5,
            "ndcg@10": self.ndcg_at_10,
        }


def compute_recall(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> float:
    """Recall@K: fraction of relevant items in top-k."""
    if not relevant_ids:
        return 0.0
    top_k = retrieved_ids[:k]
    hits = len(set(top_k) & set(relevant_ids))
    return hits / len(relevant_ids)


def compute_mrr(retrieved_ids: list[str], relevant_ids: list[str]) -> float:
    """Mean Reciprocal Rank: 1/rank of first relevant hit."""
    relevant = set(relevant_ids)
    for i, rid in enumerate(retrieved_ids):
        if rid in relevant:
            return 1.0 / (i + 1)
    return 0.0


def compute_ndcg(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> float:
    """Normalized Discounted Cumulative Gain@K."""
    relevant = set(relevant_ids)
    dcg = 0.0
    for i, rid in enumerate(retrieved_ids[:k]):
        if rid in relevant:
            dcg += 1.0 / math.log2(i + 2)
    ideal_hits = min(len(relevant_ids), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_hits))
    return dcg / idcg if idcg > 0 else 0.0


def evaluate_retrieval(
    results: list[dict[str, Any]],
    k_values: list[int] | None = None,
) -> RetrievalMetrics:
    """Evaluate retrieval across a dataset.

    Each result: {"retrieved_ids": [...], "relevant_ids": [...]}
    """
    ks = k_values or [1, 3, 5, 10]
    n = len(results)
    if n == 0:
        return RetrievalMetrics(0, 0, 0, 0, 0, 0, 0)

    recall_sums = {k: 0.0 for k in ks}
    mrr_sum = 0.0
    ndcg_5_sum = 0.0
    ndcg_10_sum = 0.0

    for r in results:
        retrieved = r.get("retrieved_ids", [])
        relevant = r.get("relevant_ids", [])
        for k in ks:
            recall_sums[k] += compute_recall(retrieved, relevant, k)
        mrr_sum += compute_mrr(retrieved, relevant)
        ndcg_5_sum += compute_ndcg(retrieved, relevant, 5)
        ndcg_10_sum += compute_ndcg(retrieved, relevant, 10)

    return RetrievalMetrics(
        recall_at_1=recall_sums[1] / n,
        recall_at_3=recall_sums[3] / n,
        recall_at_5=recall_sums[5] / n,
        recall_at_10=recall_sums[10] / n,
        mrr=mrr_sum / n,
        ndcg_at_5=ndcg_5_sum / n,
        ndcg_at_10=ndcg_10_sum / n,
    )


@dataclass
class CitationMetrics:
    precision: float
    recall: float

    def to_dict(self) -> dict[str, float]:
        return {"citation_precision": self.precision, "citation_recall": self.recall}


def compute_citation_metrics(
    predicted_citations: list[str],
    required_citations: list[str],
) -> CitationMetrics:
    """Citation precision and recall (spec §21)."""
    if not predicted_citations and not required_citations:
        return CitationMetrics(1.0, 1.0)
    predicted = set(predicted_citations)
    required = set(required_citations)
    hits = len(predicted & required)
    precision = hits / len(predicted) if predicted else 0.0
    recall = hits / len(required) if required else 0.0
    return CitationMetrics(precision=precision, recall=recall)


def load_dataset(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("dataset", data) if isinstance(data, dict) else data

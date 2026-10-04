"""Metrics for resolved evidence groups, including strict multi-chunk units."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from eval.gold_evidence import resolution_group_hit


def resolved_groups(item: dict[str, Any]) -> list[dict[str, Any]]:
    snapshot = item.get("snapshot_labels")
    groups = snapshot.get("evidence_groups") if isinstance(snapshot, dict) else None
    if isinstance(groups, list):
        return [
            group
            for group in groups
            if isinstance(group, dict)
            and group.get("status") in {"resolved", "multi_chunk"}
            and group.get("resolved_chunk_ids")
        ]
    relevant = (
        snapshot.get("relevant_chunk_ids", [])
        if isinstance(snapshot, dict)
        else item.get("relevant_chunk_ids") or []
    )
    return [{"status": "resolved", "resolved_chunk_ids": [str(chunk_id)]} for chunk_id in relevant]


def group_recall_at_k(groups: Sequence[dict[str, Any]], retrieved: Sequence[str], k: int) -> float:
    if not groups:
        return 0.0
    return sum(resolution_group_hit(group, retrieved, k) for group in groups) / len(groups)


def group_mrr(groups: Sequence[dict[str, Any]], retrieved: Sequence[str]) -> float:
    satisfaction_ranks: list[int] = []
    positions = {chunk_id: index for index, chunk_id in enumerate(retrieved, start=1)}
    for group in groups:
        required = {str(value) for value in group.get("resolved_chunk_ids", [])}
        if required and required.issubset(positions):
            satisfaction_ranks.append(max(positions[value] for value in required))
    return 1.0 / min(satisfaction_ranks) if satisfaction_ranks else 0.0


def chunk_ndcg(groups: Sequence[dict[str, Any]], retrieved: Sequence[str], k: int) -> float:
    """Binary Chunk-ID nDCG; multi-chunk strictness is handled by Recall/MRR."""
    relevant = {str(value) for group in groups for value in group.get("resolved_chunk_ids", [])}
    dcg = sum(
        1.0 / math.log2(index + 2)
        for index, chunk_id in enumerate(retrieved[:k])
        if chunk_id in relevant
    )
    ideal = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(index + 2) for index in range(ideal))
    return dcg / idcg if idcg else 0.0


@dataclass(frozen=True, slots=True)
class GoldMetrics:
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    recall_at_10: float
    mrr: float
    ndcg_at_5: float
    ndcg_at_10: float
    citation_precision: float
    citation_recall: float

    def to_dict(self) -> dict[str, float]:
        return {
            "recall@1": self.recall_at_1,
            "recall@3": self.recall_at_3,
            "recall@5": self.recall_at_5,
            "recall@10": self.recall_at_10,
            "mrr": self.mrr,
            "ndcg@5": self.ndcg_at_5,
            "ndcg@10": self.ndcg_at_10,
            "citation_precision": self.citation_precision,
            "citation_recall": self.citation_recall,
        }


def evaluate_gold_predictions(
    items: Sequence[dict[str, Any]], predictions: Sequence[dict[str, Any]]
) -> GoldMetrics:
    by_id = {str(prediction["id"]): prediction for prediction in predictions}
    answerable = [item for item in items if item.get("answerable")]
    if any(not resolved_groups(item) for item in answerable):
        raise ValueError("all answerable questions must have resolved evidence groups")
    if {str(item["id"]) for item in items} != set(by_id):
        raise ValueError("prediction IDs must match benchmark IDs")

    recalls: dict[int, list[float]] = {k: [] for k in (1, 3, 5, 10)}
    reciprocal_ranks: list[float] = []
    ndcg_5: list[float] = []
    ndcg_10: list[float] = []
    citation_hits = 0
    citation_predicted = 0
    citation_group_hits = 0
    citation_groups = 0
    for item in answerable:
        prediction = by_id[str(item["id"])]
        retrieved = [str(value) for value in prediction.get("retrieved_chunk_ids", [])]
        cited = [
            str(value)
            for value in prediction.get(
                "predicted_citation_chunk_ids", prediction.get("cited_chunk_ids", [])
            )
        ]
        groups = resolved_groups(item)
        for k, values in recalls.items():
            values.append(group_recall_at_k(groups, retrieved, k))
        reciprocal_ranks.append(group_mrr(groups, retrieved))
        ndcg_5.append(chunk_ndcg(groups, retrieved, 5))
        ndcg_10.append(chunk_ndcg(groups, retrieved, 10))
        relevant_ids = {
            str(value) for group in groups for value in group.get("resolved_chunk_ids", [])
        }
        citation_hits += len(set(cited) & relevant_ids)
        citation_predicted += len(set(cited))
        citation_group_hits += sum(
            resolution_group_hit(group, cited, len(cited)) for group in groups
        )
        citation_groups += len(groups)

    def mean(values: Sequence[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return GoldMetrics(
        recall_at_1=mean(recalls[1]),
        recall_at_3=mean(recalls[3]),
        recall_at_5=mean(recalls[5]),
        recall_at_10=mean(recalls[10]),
        mrr=mean(reciprocal_ranks),
        ndcg_at_5=mean(ndcg_5),
        ndcg_at_10=mean(ndcg_10),
        citation_precision=citation_hits / citation_predicted if citation_predicted else 0.0,
        citation_recall=citation_group_hits / citation_groups if citation_groups else 0.0,
    )

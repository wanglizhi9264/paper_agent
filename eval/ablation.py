"""Ablation runner: compare pipeline configurations (spec §21).

Runs at least: Dense; BM25; Dense+BM25+RRF; +Rerank; Full pipeline.
Also compares rewrite on/off and expansion on/off.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from eval.retrieval_eval import (
    compute_citation_metrics,
    evaluate_retrieval,
    load_dataset,
)


@dataclass
class AblationConfig:
    name: str
    use_dense: bool = True
    use_bm25: bool = True
    use_rerank: bool = True
    use_rewrite: bool = True
    use_expansion: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "use_dense": self.use_dense,
            "use_bm25": self.use_bm25,
            "use_rerank": self.use_rerank,
            "use_rewrite": self.use_rewrite,
            "use_expansion": self.use_expansion,
        }


DEFAULT_CONFIGS: list[AblationConfig] = [
    AblationConfig(
        name="dense_only", use_bm25=False, use_rerank=False, use_rewrite=False, use_expansion=False
    ),
    AblationConfig(
        name="bm25_only", use_dense=False, use_rerank=False, use_rewrite=False, use_expansion=False
    ),
    AblationConfig(name="dense_bm25_rrf", use_rerank=False, use_rewrite=False, use_expansion=False),
    AblationConfig(name="with_rerank", use_rewrite=False, use_expansion=False),
    AblationConfig(name="full_pipeline"),
    AblationConfig(name="no_rewrite", use_rewrite=False),
    AblationConfig(name="no_expansion", use_expansion=False),
]


@dataclass
class AblationResult:
    config: dict[str, Any]
    metrics: dict[str, float]
    latency_ms: float = 0.0
    citation: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config,
            "metrics": self.metrics,
            "latency_ms": self.latency_ms,
            "citation": self.citation,
        }


def run_ablation(
    dataset_path: Path,
    configs: list[AblationConfig] | None = None,
    retrieve_fn: Any | None = None,
) -> list[AblationResult]:
    """Run ablation experiments.

    ``retrieve_fn`` is a callable (config, query) -> list[str] (chunk_ids).
    If None, uses a mock that returns relevant_chunk_ids (oracle).
    """
    configs = configs or DEFAULT_CONFIGS
    dataset = load_dataset(dataset_path)
    results: list[AblationResult] = []

    for cfg in configs:
        retrieval_results: list[dict[str, Any]] = []
        citation_preds: list[str] = []
        citation_required: list[str] = []
        latencies: list[float] = []

        for item in dataset:
            query = item["question"]
            relevant = item.get("relevant_chunk_ids", [])
            required_cites = item.get("required_citation_chunk_ids", [])

            if retrieve_fn is not None:
                start = time.perf_counter()
                retrieved = retrieve_fn(cfg, query)
                latencies.append((time.perf_counter() - start) * 1000)
            else:
                # Oracle: return relevant items in order.
                retrieved = relevant[:]

            retrieval_results.append(
                {
                    "retrieved_ids": retrieved,
                    "relevant_ids": relevant,
                }
            )

            citation_preds.extend(retrieved[:5])
            citation_required.extend(required_cites)

        metrics = evaluate_retrieval(retrieval_results)
        cite_metrics = compute_citation_metrics(citation_preds, citation_required)
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

        results.append(
            AblationResult(
                config=cfg.to_dict(),
                metrics=metrics.to_dict(),
                latency_ms=avg_latency,
                citation=cite_metrics.to_dict(),
            )
        )

    return results


def format_ablation_markdown(results: list[AblationResult]) -> str:
    """Format ablation results as Markdown table (spec §21)."""
    lines = [
        "| Config | Recall@1 | Recall@5 | MRR | nDCG@10 | Citation P | Citation R | Latency ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        m = r.metrics
        c = r.citation
        lines.append(
            f"| {r.config['name']} "
            f"| {m.get('recall@1', 0):.3f} "
            f"| {m.get('recall@5', 0):.3f} "
            f"| {m.get('mrr', 0):.3f} "
            f"| {m.get('ndcg@10', 0):.3f} "
            f"| {c.get('citation_precision', 0):.3f} "
            f"| {c.get('citation_recall', 0):.3f} "
            f"| {r.latency_ms:.1f} |"
        )
    return "\n".join(lines)


def save_results(results: list[AblationResult], path: Path) -> None:
    data = {"results": [r.to_dict() for r in results]}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

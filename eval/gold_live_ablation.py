"""Evaluate live retrieval stages against a runtime-bound gold benchmark.

One production search request yields Dense, BM25, RRF, and rerank traces.  The
runner converts FAISS ids to runtime Chunk UUIDs through the API's debug map and
fails if any top-10 stage id is not mapped; it never substitutes gold labels.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx

from eval.gold_evidence_metrics import evaluate_gold_predictions, resolved_groups
from eval.pdf_v2_release import validate_resolved_dataset

STAGES = ("dense_only", "bm25_only", "dense_bm25_rrf", "rerank", "selected")
TRACE_KEYS = {
    "dense_only": "dense",
    "bm25_only": "bm25",
    "dense_bm25_rrf": "rrf",
    "rerank": "rerank",
    "selected": "selected",
}


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("dataset"), list):
        raise ValueError("dataset must be an object containing a dataset list")
    return value


def _stage_chunk_ids(debug: dict[str, Any], stage: str, *, required_top_k: int = 10) -> list[str]:
    trace_key = TRACE_KEYS[stage]
    rows = debug.get(trace_key)
    mapping = debug.get("candidate_chunk_ids")
    if not isinstance(rows, list) or not isinstance(mapping, dict):
        raise ValueError(f"missing debug trace for {stage}")
    values: list[str] = []
    for row in rows:
        if not isinstance(row, list) or not row:
            raise ValueError(f"invalid {trace_key} trace row")
        faiss_id = str(row[0])
        chunk_id = mapping.get(faiss_id)
        if chunk_id is None:
            if len(values) < required_top_k:
                raise ValueError(f"{stage}: top-{required_top_k} FAISS id {faiss_id} is unmapped")
            break
        values.append(str(chunk_id))
    return values


def _metrics(
    items: list[dict[str, Any]], predictions: dict[str, list[dict[str, Any]]]
) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for stage in STAGES:
        values = evaluate_gold_predictions(items, predictions[stage]).to_dict()
        result[stage] = {
            key: value
            for key, value in values.items()
            if key not in {"citation_precision", "citation_recall"}
        }
    return result


def _markdown(metrics: dict[str, dict[str, float]], mean_latency_ms: float) -> str:
    lines = [
        "# Gold Benchmark Live Retrieval Ablation",
        "",
        "| Pipeline | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | nDCG@5 | nDCG@10 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for stage in STAGES:
        value = metrics[stage]
        lines.append(
            f"| {stage} | {value['recall@1']:.4f} | {value['recall@3']:.4f} | "
            f"{value['recall@5']:.4f} | {value['recall@10']:.4f} | {value['mrr']:.4f} | "
            f"{value['ndcg@5']:.4f} | {value['ndcg@10']:.4f} |"
        )
    lines.extend(("", f"Mean production search latency: {mean_latency_ms:.1f} ms", ""))
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load(args.dataset)
    items = payload["dataset"]
    summary = validate_resolved_dataset(items)
    predictions: dict[str, list[dict[str, Any]]] = {stage: [] for stage in STAGES}
    latencies: list[float] = []
    with httpx.Client(base_url=args.api_base.rstrip("/"), timeout=args.timeout) as client:
        for index, item in enumerate(items, start=1):
            started = time.perf_counter()
            response = client.post(
                "/api/v1/search",
                json={
                    "query": item["question"],
                    "scope": item.get("runtime_scope", item["scope"]),
                    "top_k": 10,
                    "debug": True,
                },
            )
            response.raise_for_status()
            body = response.json()
            debug = body.get("debug")
            if not isinstance(debug, dict):
                raise ValueError(f"{item['id']}: API did not return a debug trace")
            if debug.get("snapshot_id") != summary.index_snapshot_id:
                raise ValueError(f"{item['id']}: active snapshot changed during evaluation")
            if body.get("degraded_reasons"):
                raise ValueError(f"{item['id']}: degraded retrieval is not scoreable")
            latencies.append((time.perf_counter() - started) * 1000)
            for stage in STAGES:
                predictions[stage].append(
                    {
                        "id": item["id"],
                        "retrieved_chunk_ids": _stage_chunk_ids(debug, stage),
                        "predicted_citation_chunk_ids": [],
                    }
                )
            print(f"[{index:02d}/{len(items)}] {item['id']}", flush=True)

    metrics = _metrics(items, predictions)
    mean_latency_ms = sum(latencies) / len(latencies) if latencies else 0.0
    misses = {
        stage: [
            item["id"]
            for item, prediction in zip(items, predictions[stage], strict=True)
            if item.get("answerable")
            and not any(
                set(group["resolved_chunk_ids"]).issubset(
                    set(prediction["retrieved_chunk_ids"][:10])
                )
                for group in resolved_groups(item)
            )
        ]
        for stage in STAGES
    }
    result = {
        "dataset": str(args.dataset),
        "snapshot_id": summary.index_snapshot_id,
        "answerable": summary.answerable,
        "metrics": metrics,
        "mean_search_latency_ms": mean_latency_ms,
        "top10_miss_question_ids": misses,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "retrieval_ablation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output / "retrieval_predictions.json").write_text(
        json.dumps(predictions, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output / "retrieval_ablation.md").write_text(
        _markdown(metrics, mean_latency_ms), encoding="utf-8"
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    result = run(_parser().parse_args(argv))
    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

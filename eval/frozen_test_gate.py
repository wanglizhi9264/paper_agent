"""One-shot standard-v2 frozen retrieval gate and Full Pipeline scorer.

The retrieval command is intentionally fixed to the candidate selected on dev at
commit 2231190. It refuses an existing output and never evaluates a configurable
matrix. The full-score command combines the locked retrieval traces with live
Full Pipeline predictions; it does not call retrieval or generation itself.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import dispose_engine, session_scope
from app.embedding.base import EmbeddingError
from app.embedding.registry import get_embedding_provider
from app.llm.base import LLMError
from app.llm.openai_compatible import get_llm_provider
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.index_snapshot import SystemState
from app.rerank.base import RerankError
from app.schemas.search import SearchRequest, SearchScope
from app.services.retrieval import search_corpus
from app.services.retrieval_queries import build_retrieval_queries
from eval.cross_language_retrieval import _trace_chunk_ids
from eval.evidence_selection import retrieval_query
from eval.gold_evidence_metrics import (
    evaluate_gold_predictions,
    group_recall_at_k,
    resolved_groups,
)
from eval.pdf_v2_release import validate_resolved_dataset
from eval.ranking_pipeline_ab import TRACE_STAGES, classify_failure

CANDIDATE_COMMIT = "2231190"
CANDIDATE_CONFIG: dict[str, object] = {
    "retrieval_dense_top_k": 100,
    "retrieval_bm25_top_k": 100,
    "retrieval_rrf_top_k": 100,
    "retrieval_rerank_top_k": 30,
    "retrieval_cross_language_fusion": "four_way_rrf",
    "retrieval_candidate_rescue": "retriever_top_m",
    "retrieval_candidate_rescue_top_m": 10,
    "retrieval_rerank_query": "original",
    "retrieval_selection": "legacy",
    "retrieval_document_balance": "explicit_scope",
    "chat_cross_language_retrieval": "cjk_to_english_dual",
    "chat_retrieval_workflow": "single_pass",
    "chat_rewrite_language": "preserve",
}
FAILURE_NAMES = {
    "NOT_IN_RAW_UNION": "RAW_UNION_MISS",
    "LOST_IN_FUSION": "LOST_IN_FUSION",
    "LOST_BEFORE_RERANK_DEPTH": "LOST_BEFORE_RERANK_DEPTH",
    "LOST_IN_RERANK": "LOST_IN_RERANK",
    "SURVIVED_TOP10": "FINAL_TOP10_HIT",
}
RETRIEVAL_IMPLEMENTATION_PATHS = (
    "app/core/config.py",
    "app/retrieval/fusion.py",
    "app/services/retrieval.py",
    "app/services/retrieval_queries.py",
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("dataset")
    if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
        raise ValueError("dataset must be a list of objects")
    return values


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def verify_locked_implementation() -> None:
    subprocess.run(["git", "merge-base", "--is-ancestor", CANDIDATE_COMMIT, "HEAD"], check=True)
    changed = _git("diff", CANDIDATE_COMMIT, "--", *RETRIEVAL_IMPLEMENTATION_PATHS)
    if changed:
        raise ValueError("Retrieval implementation changed after candidate lock")


def _raw_union(traces: dict[str, list[str]], depth: int) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for stage in ("dense_original", "dense_english", "bm25_original", "bm25_english"):
        for chunk_id in traces[stage][:depth]:
            if chunk_id not in seen:
                seen.add(chunk_id)
                values.append(chunk_id)
    return values


def _rank_map(gold: set[str], ranked: list[str]) -> dict[str, int | None]:
    positions = {chunk_id: rank for rank, chunk_id in enumerate(ranked, start=1)}
    return {chunk_id: positions.get(chunk_id) for chunk_id in sorted(gold)}


def summarize_frozen_retrieval(
    items: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    predictions = [
        {
            "id": row["id"],
            "retrieved_chunk_ids": row["traces"]["selected"],
            "predicted_citation_chunk_ids": [],
        }
        for row in rows
    ]
    metrics = evaluate_gold_predictions(items, predictions).to_dict()
    categories = Counter(FAILURE_NAMES[row["failure_category"]] for row in rows)
    return {
        **{key: value for key, value in metrics.items() if not key.startswith("citation_")},
        "candidate_union": {
            f"recall@{depth}": statistics.fmean(
                group_recall_at_k(resolved_groups(item), _raw_union(row["traces"], depth), 10_000)
                for item, row in zip(items, rows, strict=True)
            )
            for depth in (30, 50, 100)
        },
        "rrf_recall@30": statistics.fmean(
            group_recall_at_k(resolved_groups(item), row["traces"]["fused"], 30)
            for item, row in zip(items, rows, strict=True)
        ),
        "rrf_recall@50": statistics.fmean(
            group_recall_at_k(resolved_groups(item), row["traces"]["fused"], 50)
            for item, row in zip(items, rows, strict=True)
        ),
        "final_rerank_recall@10": metrics["recall@10"],
        "failure_stages": {name: categories.get(name, 0) for name in FAILURE_NAMES.values()},
        "mean_latency_ms": statistics.fmean(
            float(row["timings_ms"]["total"]) + float(row["timings_ms"].get("translation", 0.0))
            for row in rows
        ),
    }


async def run_frozen_retrieval(dataset: Path) -> dict[str, Any]:
    verify_locked_implementation()
    payload = _load(dataset)
    contract = validate_resolved_dataset(payload)
    items = [
        item
        for item in _items(payload)
        if item.get("split") == "test" and item.get("answerable") is True
    ]
    if len(items) != 15:
        raise ValueError(f"Expected 15 answerable frozen-test cases, got {len(items)}")

    settings = get_settings()
    if settings.env == "test" or "fake" in {
        settings.embedding_model,
        settings.rerank_model,
        settings.llm_model,
    }:
        raise ValueError("Frozen gate requires real configured models")
    candidate = settings.model_copy(update=CANDIDATE_CONFIG)
    rows: list[dict[str, Any]] = []
    async with session_scope() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        state = await session.get(SystemState, 1)
        if state is None or str(state.active_index_snapshot_id) != contract.index_snapshot_id:
            raise ValueError("Active snapshot does not match frozen labels")
        chunk_rows = (
            await session.execute(
                select(Chunk, Document)
                .join(Document, Document.id == Chunk.document_id)
                .where(Chunk.document_version_id == Document.active_document_version_id)
            )
        ).all()
        chunk_docs = {str(chunk.id): str(chunk.document_id) for chunk, _document in chunk_rows}
        for item in items:
            scope = SearchScope.model_validate(item["runtime_scope"])
            allowed = {str(value) for value in scope.document_ids}
            gold = set(item["snapshot_labels"]["relevant_chunk_ids"])
            if scope.type != "documents" or any(
                chunk_docs.get(chunk_id) not in allowed for chunk_id in gold
            ):
                raise ValueError(f"{item['id']}: Gold is outside frozen scope")

        embedder = get_embedding_provider(settings)
        llm = get_llm_provider(settings)
        effort = (
            None
            if settings.chat_planner_reasoning_effort == "provider_default"
            else settings.chat_planner_reasoning_effort
        )
        for number, item in enumerate(items, start=1):
            outcome = await build_retrieval_queries(
                llm,
                retrieval_query(item),
                mode="cjk_to_english_dual",
                reasoning_effort=effort,
            )
            if outcome.degraded_reasons:
                raise ValueError(f"{item['id']}: translation degraded")
            response = await search_corpus(
                session,
                SearchRequest(
                    query=retrieval_query(item),
                    scope=SearchScope.model_validate(item["runtime_scope"]),
                    top_k=10,
                    debug=True,
                ),
                embedder,
                original_query=item["question"],
                settings=candidate,
                retrieval_queries=outcome.queries,
                translation_latency_ms=outcome.translation_ms,
            )
            if response.degraded_reasons or response.debug is None:
                raise ValueError(f"{item['id']}: retrieval degraded or debug missing")
            debug = response.debug
            if debug.get("snapshot_id") != contract.index_snapshot_id:
                raise ValueError(f"{item['id']}: snapshot changed during gate")
            traces: dict[str, list[str]] = {}
            for stage in TRACE_STAGES:
                trace_stage = stage
                if stage == "dense_merged" and debug.get(stage) is None:
                    trace_stage = "dense_original"
                if stage == "bm25_merged" and debug.get(stage) is None:
                    trace_stage = "bm25_original"
                traces[stage] = _trace_chunk_ids(debug, trace_stage)
            gold = set(item["snapshot_labels"]["relevant_chunk_ids"])
            rows.append(
                {
                    "id": item["id"],
                    "traces": traces,
                    "gold_ranks": {
                        stage: _rank_map(gold, ranked) for stage, ranked in traces.items()
                    },
                    "failure_category": classify_failure(item, traces),
                    "timings_ms": debug["timings_ms"],
                    "retrieval_queries": debug["retrieval_queries"],
                }
            )
            print(f"frozen retrieval {number}/15 {item['id']}", flush=True)

    return {
        "schema_version": 1,
        "candidate_commit": CANDIDATE_COMMIT,
        "execution_commit": _git("rev-parse", "HEAD"),
        "created_at": datetime.now(UTC).isoformat(),
        "split": "test",
        "answerable_count": len(items),
        "test_evaluated_once": True,
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "snapshot_id": contract.index_snapshot_id,
        "config": CANDIDATE_CONFIG,
        "summary": summarize_frozen_retrieval(items, rows),
        "questions": rows,
    }


def _retrieval_map(
    dev_result: dict[str, Any], frozen_result: dict[str, Any]
) -> dict[str, list[str]]:
    dev_rows = dev_result["questions"]["B2_rescue_10"]
    return {
        str(row["id"]): [str(value) for value in row["traces"]["selected"]]
        for row in [*dev_rows, *frozen_result["questions"]]
    }


def _split_metrics(
    items: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    metrics = evaluate_gold_predictions(items, predictions).to_dict()
    unanswerable = [item for item in items if not item["answerable"]]
    by_id = {str(prediction["id"]): prediction for prediction in predictions}
    rejected = sum(
        by_id[str(item["id"])].get("rejected_unanswerable") is True for item in unanswerable
    )
    errors = sum(bool(prediction.get("error_category")) for prediction in predictions)
    latencies = [float(prediction.get("latency_ms") or 0.0) for prediction in predictions]
    return {
        **metrics,
        "unanswerable_rejection": rejected / len(unanswerable) if unanswerable else 0.0,
        "prediction_errors": errors,
        "mean_end_to_end_latency_ms": statistics.fmean(latencies) if latencies else 0.0,
    }


def citation_diagnostics(
    items: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    by_id = {str(prediction["id"]): prediction for prediction in predictions}
    counts = Counter()
    misses: list[dict[str, Any]] = []
    for item in items:
        if not item["answerable"]:
            continue
        prediction = by_id[str(item["id"])]
        retrieved = [str(value) for value in prediction.get("retrieved_chunk_ids") or []]
        cited = [str(value) for value in prediction.get("predicted_citation_chunk_ids") or []]
        groups = resolved_groups(item)
        retrieval_hit = any(group_recall_at_k([group], retrieved, 10) == 1.0 for group in groups)
        citation_hit = any(group_recall_at_k([group], cited, len(cited)) == 1.0 for group in groups)
        if not retrieval_hit:
            counts["retrieval_miss"] += 1
        elif citation_hit:
            counts["retrieval_hit_citation_hit"] += 1
        else:
            counts["retrieval_hit_citation_miss"] += 1
            relevant = {
                str(value) for group in groups for value in group.get("resolved_chunk_ids", [])
            }
            positions = {chunk_id: rank for rank, chunk_id in enumerate(retrieved, start=1)}
            misses.append(
                {
                    "question_id": item["id"],
                    "split": item["split"],
                    "gold_evidence_rank": {
                        chunk_id: positions.get(chunk_id) for chunk_id in sorted(relevant)
                    },
                    "selected_context_ranks": [
                        {"rank": rank, "chunk_id": chunk_id}
                        for rank, chunk_id in enumerate(retrieved[:10], start=1)
                    ],
                    "generated_citations": cited,
                    "missed_gold_chunks": sorted(relevant - set(cited)),
                }
            )
    return {
        "counts": {
            "retrieval_miss": counts["retrieval_miss"],
            "retrieval_hit_citation_miss": counts["retrieval_hit_citation_miss"],
            "retrieval_hit_citation_hit": counts["retrieval_hit_citation_hit"],
        },
        "retrieval_hit_citation_miss_samples": misses,
    }


def score_full_pipeline(
    dataset: Path,
    dev_result_path: Path,
    frozen_result_path: Path,
    predictions_path: Path,
) -> dict[str, Any]:
    payload = _load(dataset)
    validate_resolved_dataset(payload)
    items = _items(payload)
    dev_result = _load(dev_result_path)
    frozen_result = _load(frozen_result_path)
    prediction_payload = _load(predictions_path)
    raw_predictions = prediction_payload.get("predictions")
    if not isinstance(raw_predictions, list) or len(raw_predictions) != 60:
        raise ValueError("Full Pipeline predictions must contain exactly 60 rows")
    retrieval = _retrieval_map(dev_result, frozen_result)
    predictions: list[dict[str, Any]] = []
    for raw in raw_predictions:
        prediction = dict(raw)
        if prediction.get("id") in retrieval:
            prediction["retrieved_chunk_ids"] = retrieval[str(prediction["id"])]
        predictions.append(prediction)

    by_split: dict[str, Any] = {}
    for split in ("dev", "test", "overall"):
        selected_items = items if split == "overall" else [i for i in items if i["split"] == split]
        ids = {str(item["id"]) for item in selected_items}
        selected_predictions = [p for p in predictions if str(p["id"]) in ids]
        by_split[split] = {
            "metrics": _split_metrics(selected_items, selected_predictions),
            "citation_diagnostics": citation_diagnostics(selected_items, selected_predictions),
        }
    return {
        "schema_version": 1,
        "candidate_commit": CANDIDATE_COMMIT,
        "created_at": datetime.now(UTC).isoformat(),
        "retrieval_source": {
            "dev": str(dev_result_path),
            "test": str(frozen_result_path),
        },
        "full_pipeline_predictions": str(predictions_path),
        "splits": by_split,
    }


def _retrieval_markdown(result: dict[str, Any]) -> str:
    value = result["summary"]
    failures = value["failure_stages"]
    return "\n".join(
        (
            "# Frozen Retrieval Gate",
            "",
            "The standard-v2 frozen test was evaluated once with the locked candidate.",
            "",
            f"- Recall@1/3/5/10: {value['recall@1']:.4f} / {value['recall@3']:.4f} / {value['recall@5']:.4f} / {value['recall@10']:.4f}",
            f"- MRR: {value['mrr']:.4f}",
            f"- nDCG@5/10: {value['ndcg@5']:.4f} / {value['ndcg@10']:.4f}",
            f"- Candidate union R@30/50/100: {value['candidate_union']['recall@30']:.4f} / {value['candidate_union']['recall@50']:.4f} / {value['candidate_union']['recall@100']:.4f}",
            f"- RRF R@30/50: {value['rrf_recall@30']:.4f} / {value['rrf_recall@50']:.4f}",
            f"- Final rerank R@10: {value['final_rerank_recall@10']:.4f}",
            f"- Mean latency: {value['mean_latency_ms']:.1f} ms",
            f"- Failure stages: {json.dumps(failures, sort_keys=True)}",
            "",
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    retrieval = subparsers.add_parser("retrieval")
    retrieval.add_argument("--dataset", type=Path, required=True)
    retrieval.add_argument("--output-dir", type=Path, required=True)
    retrieval.add_argument("--allow-local-models", action="store_true")
    retrieval.add_argument("--allow-llm-calls", action="store_true")
    retrieval.add_argument("--confirm-frozen-once", action="store_true")

    full = subparsers.add_parser("full-score")
    full.add_argument("--dataset", type=Path, required=True)
    full.add_argument("--dev-result", type=Path, required=True)
    full.add_argument("--frozen-result", type=Path, required=True)
    full.add_argument("--predictions", type=Path, required=True)
    full.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "full-score":
        if args.output.exists():
            raise SystemExit("Output exists; refusing to overwrite")
        result = score_full_pipeline(
            args.dataset, args.dev_result, args.frozen_result, args.predictions
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result["splits"], ensure_ascii=False, indent=2))
        return
    if not (args.allow_local_models and args.allow_llm_calls and args.confirm_frozen_once):
        raise SystemExit("Frozen retrieval requires all three explicit confirmation flags")
    if args.output_dir.exists():
        raise SystemExit("Output directory exists; refusing a repeat frozen-test run")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    async def run() -> dict[str, Any]:
        try:
            return await run_frozen_retrieval(args.dataset)
        finally:
            await dispose_engine()

    try:
        result = asyncio.run(run())
    except (OSError, SQLAlchemyError, ValueError, EmbeddingError, RerankError, LLMError) as exc:
        print(f"FROZEN_GATE_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    args.output_dir.mkdir(parents=True)
    (args.output_dir / "frozen-retrieval.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output_dir / "frozen-retrieval.md").write_text(
        _retrieval_markdown(result), encoding="utf-8"
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

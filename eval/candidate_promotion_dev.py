"""Dev-only A/B for strict-30 source-aware candidate promotion."""

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

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.db.session import dispose_engine, session_scope
from app.embedding.base import EmbeddingError
from app.embedding.registry import get_embedding_provider
from app.llm.base import LLMError
from app.llm.openai_compatible import get_llm_provider
from app.models.index_snapshot import SystemState
from app.rerank.base import RerankError
from app.schemas.search import SearchRequest, SearchScope
from app.services.retrieval import search_corpus
from app.services.retrieval_queries import RetrievalQueries, build_retrieval_queries
from eval.cross_language_retrieval import _trace_chunk_ids, dev_answerable_items
from eval.evidence_selection import retrieval_query
from eval.gold_evidence_metrics import evaluate_gold_predictions, group_recall_at_k, resolved_groups
from eval.pdf_v2_release import validate_resolved_dataset
from eval.ranking_pipeline_ab import TRACE_STAGES, classify_failure

SOURCE_STAGES = ("dense_original", "dense_english", "bm25_original", "bm25_english")
VARIANTS: dict[str, dict[str, object]] = {
    "baseline": {"retrieval_candidate_promotion": "off"},
    "M2": {
        "retrieval_candidate_promotion": "source_top_m",
        "retrieval_candidate_promotion_top_m": 2,
    },
    "M3": {
        "retrieval_candidate_promotion": "source_top_m",
        "retrieval_candidate_promotion_top_m": 3,
    },
    "M5": {
        "retrieval_candidate_promotion": "source_top_m",
        "retrieval_candidate_promotion_top_m": 5,
    },
}


def _rank_map(gold: set[str], ranked: list[str]) -> dict[str, int | None]:
    positions = {chunk_id: rank for rank, chunk_id in enumerate(ranked, start=1)}
    return {chunk_id: positions.get(chunk_id) for chunk_id in sorted(gold)}


def _best_rank(values: dict[str, int | None]) -> int | None:
    ranks = [rank for rank in values.values() if rank is not None]
    return min(ranks) if ranks else None


def summarize(items: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    predictions = [
        {
            "id": row["id"],
            "retrieved_chunk_ids": row["traces"]["selected"],
            "predicted_citation_chunk_ids": [],
        }
        for row in rows
    ]
    metrics = evaluate_gold_predictions(items, predictions).to_dict()
    categories = Counter(row["failure_category"] for row in rows)
    return {
        "recall@10": metrics["recall@10"],
        "mrr": metrics["mrr"],
        "ndcg@10": metrics["ndcg@10"],
        "reranker_input_candidate_recall": statistics.fmean(
            group_recall_at_k(resolved_groups(item), row["traces"]["rerank_input"], 10_000)
            for item, row in zip(items, rows, strict=True)
        ),
        "LOST_BEFORE_RERANK_DEPTH": categories["LOST_BEFORE_RERANK_DEPTH"],
        "LOST_IN_RERANK": categories["LOST_IN_RERANK"],
        "FINAL_TOP10_HIT": categories["SURVIVED_TOP10"],
        "mean_candidate_pool_size": statistics.fmean(
            len(row["traces"]["rerank_input"]) for row in rows
        ),
        "mean_latency_ms": statistics.fmean(
            float(row["timings_ms"]["total"]) + float(row["timings_ms"].get("translation", 0.0))
            for row in rows
        ),
    }


def analyze_depth_losses(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    winners: Counter[str] = Counter()
    for row in rows:
        if row["failure_category"] != "LOST_BEFORE_RERANK_DEPTH":
            continue
        ranks = {stage: _best_rank(row["gold_ranks"][stage]) for stage in SOURCE_STAGES}
        finite = {stage: rank for stage, rank in ranks.items() if rank is not None}
        best = min(finite.values()) if finite else None
        best_sources = sorted(stage for stage, rank in finite.items() if rank == best)
        winners.update(best_sources)
        cases.append(
            {
                "question_id": row["id"],
                "source_gold_ranks": ranks,
                "best_rank": best,
                "best_sources": best_sources,
                "fused_gold_rank": _best_rank(row["gold_ranks"]["fused"]),
            }
        )
    return {
        "count": len(cases),
        "best_source_counts": dict(sorted(winners.items())),
        "cases": cases,
    }


async def evaluate(dataset: Path) -> dict[str, Any]:
    payload = json.loads(dataset.read_text(encoding="utf-8"))
    contract = validate_resolved_dataset(payload)
    items = dev_answerable_items(payload)
    if len(items) != 37 or any(item.get("split") != "dev" for item in items):
        raise ValueError("Promotion A/B requires exactly 37 answerable dev cases")
    settings = get_settings()
    if settings.env == "test" or "fake" in {
        settings.embedding_model,
        settings.rerank_model,
        settings.llm_model,
    }:
        raise ValueError("Real dev evaluation requires real models")
    base_overrides: dict[str, object] = {
        "retrieval_dense_top_k": 100,
        "retrieval_bm25_top_k": 100,
        "retrieval_rrf_top_k": 100,
        "retrieval_rerank_top_k": 30,
        "retrieval_cross_language_fusion": "four_way_rrf",
        "retrieval_candidate_rescue": "retriever_top_m",
        "retrieval_candidate_rescue_top_m": 10,
        "retrieval_rerank_query": "original",
    }
    rows: dict[str, list[dict[str, Any]]] = {}
    async with session_scope() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        state = await session.get(SystemState, 1)
        if state is None or str(state.active_index_snapshot_id) != contract.index_snapshot_id:
            raise ValueError("Active snapshot does not match frozen labels")
        embedder = get_embedding_provider(settings)
        llm = get_llm_provider(settings)
        effort = (
            None
            if settings.chat_planner_reasoning_effort == "provider_default"
            else settings.chat_planner_reasoning_effort
        )
        plans: dict[str, tuple[RetrievalQueries, float]] = {}
        for item in items:
            outcome = await build_retrieval_queries(
                llm,
                retrieval_query(item),
                mode="cjk_to_english_dual",
                reasoning_effort=effort,
            )
            if outcome.degraded_reasons:
                raise ValueError(f"{item['id']}: translation degraded")
            plans[str(item["id"])] = (outcome.queries, outcome.translation_ms)

        for name, overrides in VARIANTS.items():
            variant = settings.model_copy(update={**base_overrides, **overrides})
            values: list[dict[str, Any]] = []
            for number, item in enumerate(items, start=1):
                plan, translation_ms = plans[str(item["id"])]
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
                    settings=variant,
                    retrieval_queries=plan,
                    translation_latency_ms=translation_ms,
                )
                if response.degraded_reasons or response.debug is None:
                    raise ValueError(f"{name}/{item['id']}: degraded retrieval")
                debug = response.debug
                if debug.get("snapshot_id") != contract.index_snapshot_id:
                    raise ValueError("Snapshot changed during promotion A/B")
                traces: dict[str, list[str]] = {}
                for stage in TRACE_STAGES:
                    trace_stage = stage
                    if stage == "dense_merged" and debug.get(stage) is None:
                        trace_stage = "dense_original"
                    if stage == "bm25_merged" and debug.get(stage) is None:
                        trace_stage = "bm25_original"
                    traces[stage] = _trace_chunk_ids(debug, trace_stage)
                gold = set(item["snapshot_labels"]["relevant_chunk_ids"])
                values.append(
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
                print(f"{name} {number}/37 {item['id']}", flush=True)
            rows[name] = values

    summaries = {name: summarize(items, values) for name, values in rows.items()}
    return {
        "schema_version": 1,
        "split": "dev",
        "test_evaluated": False,
        "created_at": datetime.now(UTC).isoformat(),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "snapshot_id": contract.index_snapshot_id,
        "base_config": base_overrides,
        "variants": VARIANTS,
        "baseline_depth_loss_analysis": analyze_depth_losses(rows["baseline"]),
        "summaries": summaries,
        "questions": rows,
    }


def _markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Candidate Promotion Dev A/B",
        "",
        "Frozen test was not evaluated.",
        "",
        "| Variant | R@10 | MRR | nDCG@10 | Input recall | Depth loss | Rerank loss | Top10 hit | Pool | Latency ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, value in result["summaries"].items():
        lines.append(
            f"| {name} | {value['recall@10']:.4f} | {value['mrr']:.4f} | "
            f"{value['ndcg@10']:.4f} | {value['reranker_input_candidate_recall']:.4f} | "
            f"{value['LOST_BEFORE_RERANK_DEPTH']} | {value['LOST_IN_RERANK']} | "
            f"{value['FINAL_TOP10_HIT']} | {value['mean_candidate_pool_size']:.1f} | "
            f"{value['mean_latency_ms']:.1f} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-local-models", action="store_true")
    parser.add_argument("--allow-llm-calls", action="store_true")
    args = parser.parse_args()
    if not args.allow_local_models or not args.allow_llm_calls:
        parser.error("Both model/LLM confirmation flags are required")
    if args.output_dir.exists():
        parser.error("Output exists; choose a new dev output directory")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    async def run() -> dict[str, Any]:
        try:
            return await evaluate(args.dataset)
        finally:
            await dispose_engine()

    try:
        result = asyncio.run(run())
    except (OSError, SQLAlchemyError, ValueError, EmbeddingError, RerankError, LLMError) as exc:
        print(f"PROMOTION_DEV_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    args.output_dir.mkdir(parents=True)
    (args.output_dir / "dev-results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output_dir / "dev-report.md").write_text(_markdown(result), encoding="utf-8")
    print(json.dumps(result["summaries"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

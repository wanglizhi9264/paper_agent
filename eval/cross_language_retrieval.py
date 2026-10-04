"""Dev-only A/B for optional CJK-to-English dual-query retrieval.

The evaluator is read-only, rejects non-dev samples, uses the same frozen
snapshot/scope/models/depths for both variants, and never generates answers.
Gold labels are used only after retrieval for scoring.
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
from eval.evidence_selection import retrieval_query
from eval.gold_evidence_metrics import (
    evaluate_gold_predictions,
    group_recall_at_k,
    resolved_groups,
)
from eval.pdf_v2_release import validate_resolved_dataset

VARIANTS = ("off", "cjk_to_english_dual")
STAGES = (
    "dense_original",
    "dense_english",
    "bm25_original",
    "bm25_english",
    "rrf",
    "rerank",
    "selected",
)
DEPTHS = (10, 30, 50, 100)
HARD_MISS_IDS = frozenset(
    {
        "eval-001",
        "eval-005",
        "eval-007",
        "eval-011",
        "eval-012",
        "eval-015",
        "eval-021",
        "eval-033",
        "eval-037",
        "eval-043",
        "eval-044",
        "eval-049",
        "eval-052",
    }
)


def dev_answerable_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Select only scoreable dev rows; held-out test rows are never returned."""
    items = payload.get("dataset")
    if not isinstance(items, list):
        raise ValueError("Dataset payload must contain a dataset list")
    return [
        item
        for item in items
        if isinstance(item, dict) and item.get("answerable") and item.get("split") == "dev"
    ]


def _trace_chunk_ids(debug: dict[str, Any], stage: str) -> list[str]:
    rows = debug.get(stage)
    mapping = debug.get("candidate_chunk_ids")
    if not isinstance(rows, list) or not isinstance(mapping, dict):
        raise ValueError(f"Missing or invalid debug trace for {stage}")
    chunk_ids: list[str] = []
    for row in rows:
        if not isinstance(row, list | tuple) or not row:
            raise ValueError(f"Invalid {stage} trace row")
        chunk_id = mapping.get(str(row[0]))
        if chunk_id is None:
            raise ValueError(f"Unmapped FAISS id in {stage}: {row[0]}")
        chunk_ids.append(str(chunk_id))
    return chunk_ids


def _candidate_union(stage_ids: dict[str, list[str]], depth: int) -> set[str]:
    return {
        chunk_id
        for stage in (
            "dense_original",
            "dense_english",
            "bm25_original",
            "bm25_english",
        )
        for chunk_id in stage_ids[stage][:depth]
    }


def _rank_map(gold_ids: set[str], ranked: list[str]) -> dict[str, int | None]:
    positions = {chunk_id: rank for rank, chunk_id in enumerate(ranked, start=1)}
    return {chunk_id: positions.get(chunk_id) for chunk_id in sorted(gold_ids)}


def _stage_summary(
    items: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    stage: str,
    *,
    trace_field: str,
    rank_field: str,
) -> dict[str, Any]:
    predictions = [
        {
            "id": row["id"],
            "retrieved_chunk_ids": row[trace_field][stage],
            "predicted_citation_chunk_ids": [],
        }
        for row in rows
    ]
    base = evaluate_gold_predictions(items, predictions).to_dict()
    metrics: dict[str, Any] = {
        key: value
        for key, value in base.items()
        if key not in {"citation_precision", "citation_recall"}
    }
    for depth in DEPTHS:
        recalls = [
            group_recall_at_k(resolved_groups(item), row[trace_field][stage], depth)
            for item, row in zip(items, rows, strict=True)
        ]
        metrics[f"recall@{depth}"] = statistics.fmean(recalls)
    observed_ranks = [
        rank for row in rows for rank in row[rank_field][stage].values() if rank is not None
    ]
    metrics["gold_rank"] = {
        "mean": statistics.fmean(observed_ranks) if observed_ranks else None,
        "median": statistics.median(observed_ranks) if observed_ranks else None,
        "observed": len(observed_ranks),
        "missing": sum(rank is None for row in rows for rank in row[rank_field][stage].values()),
    }
    return metrics


def summarize_variant(items: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows or len(items) != len(rows):
        raise ValueError("Variant rows must align with non-empty dev items")
    candidate_recall: dict[str, float] = {}
    candidate_misses: dict[str, list[str]] = {}
    for depth in DEPTHS:
        values: list[float] = []
        misses: list[str] = []
        for item, row in zip(items, rows, strict=True):
            groups = resolved_groups(item)
            pool = _candidate_union(row["stage_chunk_ids"], depth)
            recall = group_recall_at_k(groups, list(pool), len(pool))
            values.append(recall)
            if recall == 0.0:
                misses.append(str(item["id"]))
        candidate_recall[f"per_list@{depth}"] = statistics.fmean(values)
        candidate_misses[f"per_list@{depth}"] = misses

    reasons = Counter(reason for row in rows for reason in row["degraded_reasons"])
    production_trace = (
        "production_stage_chunk_ids"
        if "production_stage_chunk_ids" in rows[0]
        else "stage_chunk_ids"
    )
    production_ranks = (
        "production_gold_ranks" if "production_gold_ranks" in rows[0] else "gold_ranks"
    )
    production_timing = (
        "production_timings_ms" if "production_timings_ms" in rows[0] else "timings_ms"
    )
    diagnostic_timing = (
        "diagnostic_timings_ms" if "diagnostic_timings_ms" in rows[0] else "timings_ms"
    )

    def mean_timings(field: str) -> dict[str, float]:
        keys = sorted({key for row in rows for key in row.get(field, {})})
        return {
            key: statistics.fmean(float(row[field].get(key, 0.0)) for row in rows) for key in keys
        }

    return {
        "n": len(rows),
        "production_stages": {
            stage: _stage_summary(
                items,
                rows,
                stage,
                trace_field=production_trace,
                rank_field=production_ranks,
            )
            for stage in STAGES
        },
        "diagnostic_stages": {
            stage: _stage_summary(
                items,
                rows,
                stage,
                trace_field="stage_chunk_ids",
                rank_field="gold_ranks",
            )
            for stage in STAGES
        },
        "candidate_union_recall": candidate_recall,
        "candidate_pool_miss_ids": candidate_misses,
        "translation_failure_rate": sum(
            "CROSS_LANGUAGE_REWRITE_FAILED" in row["degraded_reasons"] for row in rows
        )
        / len(rows),
        "degraded_reasons": dict(sorted(reasons.items())),
        "production_mean_timings_ms": mean_timings(production_timing),
        "diagnostic_mean_timings_ms": mean_timings(diagnostic_timing),
        "production_mean_end_to_end_ms": statistics.fmean(
            float(row[production_timing].get("total", 0.0))
            + float(row[production_timing].get("translation", 0.0))
            for row in rows
        ),
    }


def decide(summary: dict[str, Any]) -> dict[str, Any]:
    off = summary["off"]
    dual = summary["cjk_to_english_dual"]
    recall_delta = (
        dual["production_stages"]["rerank"]["recall@10"]
        - off["production_stages"]["rerank"]["recall@10"]
    )
    mrr_delta = (
        dual["production_stages"]["rerank"]["mrr"] - off["production_stages"]["rerank"]["mrr"]
    )
    keep = recall_delta > 0 and mrr_delta >= -0.01 and dual["translation_failure_rate"] <= 0.20
    return {
        "decision": "KEEP OPT-IN" if keep else "REJECT",
        "rule": (
            "Keep only when rerank Recall@10 improves, rerank MRR regression is <= 0.01, "
            "and translation failure rate is <= 20%."
        ),
        "rerank_recall@10_delta": recall_delta,
        "rerank_mrr_delta": mrr_delta,
        "candidate_union_recall@50_delta": (
            dual["candidate_union_recall"]["per_list@50"]
            - off["candidate_union_recall"]["per_list@50"]
        ),
    }


def hard_miss_comparison(rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    by_variant = {variant: {row["id"]: row for row in values} for variant, values in rows.items()}
    available = sorted(HARD_MISS_IDS & set(by_variant["off"]))
    details = []
    for case_id in available:
        before = by_variant["off"][case_id]
        after = by_variant["cjk_to_english_dual"][case_id]

        def candidate_hit(row: dict[str, Any], depth: int) -> bool:
            pool = _candidate_union(row["stage_chunk_ids"], depth)
            return any(set(group).issubset(pool) for group in row["gold_evidence_groups"])

        def final_hit(row: dict[str, Any]) -> bool:
            retrieved = set(row["production_stage_chunk_ids"]["rerank"][:10])
            return any(set(group).issubset(retrieved) for group in row["gold_evidence_groups"])

        def rrf_hit(row: dict[str, Any], depth: int) -> bool:
            retrieved = set(row["stage_chunk_ids"]["rrf"][:depth])
            return any(set(group).issubset(retrieved) for group in row["gold_evidence_groups"])

        details.append(
            {
                "id": case_id,
                "off_candidate_hit@30": candidate_hit(before, 30),
                "dual_candidate_hit@30": candidate_hit(after, 30),
                "off_candidate_hit@50": candidate_hit(before, 50),
                "dual_candidate_hit@50": candidate_hit(after, 50),
                "off_rrf_hit@30": rrf_hit(before, 30),
                "dual_rrf_hit@30": rrf_hit(after, 30),
                "off_rrf_hit@50": rrf_hit(before, 50),
                "dual_rrf_hit@50": rrf_hit(after, 50),
                "off_final_hit@10": final_hit(before),
                "dual_final_hit@10": final_hit(after),
                "off_rerank_gold_ranks": before["gold_ranks"]["rerank"],
                "dual_rerank_gold_ranks": after["gold_ranks"]["rerank"],
            }
        )
    return {
        "expected_slice_size": len(HARD_MISS_IDS),
        "evaluated_slice_size": len(available),
        "recovered_in_candidate_union@30": sum(
            not row["off_candidate_hit@30"] and row["dual_candidate_hit@30"] for row in details
        ),
        "recovered_in_candidate_union@50": sum(
            not row["off_candidate_hit@50"] and row["dual_candidate_hit@50"] for row in details
        ),
        "recovered_in_rrf@30": sum(
            not row["off_rrf_hit@30"] and row["dual_rrf_hit@30"] for row in details
        ),
        "recovered_in_rrf@50": sum(
            not row["off_rrf_hit@50"] and row["dual_rrf_hit@50"] for row in details
        ),
        "recovered_in_final_rerank@10": sum(
            not row["off_final_hit@10"] and row["dual_final_hit@10"] for row in details
        ),
        "questions": details,
    }


def candidate_recovery(rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    by_variant = {variant: {row["id"]: row for row in values} for variant, values in rows.items()}
    result: dict[str, Any] = {}
    for depth in (30, 50, 100):
        recovered: list[str] = []
        for case_id, before in by_variant["off"].items():
            after = by_variant["cjk_to_english_dual"][case_id]
            groups = [set(group) for group in before["gold_evidence_groups"]]
            before_pool = _candidate_union(before["stage_chunk_ids"], depth)
            after_pool = _candidate_union(after["stage_chunk_ids"], depth)
            before_hit = any(group.issubset(before_pool) for group in groups)
            after_hit = any(group.issubset(after_pool) for group in groups)
            if not before_hit and after_hit:
                recovered.append(case_id)
        result[f"per_list@{depth}"] = {"count": len(recovered), "question_ids": recovered}
    return result


def _markdown(result: dict[str, Any]) -> str:
    lines = [
        "# CJK→English Dual-query Retrieval Dev A/B",
        "",
        "Frozen test was not evaluated. This is a retrieval-only dev experiment; citation metrics were not run.",
        "",
        "| Variant | Rerank R@1 | R@3 | R@5 | R@10 | MRR | nDCG@5 | nDCG@10 | Mean E2E ms | Fallback |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for variant in VARIANTS:
        summary = result["variants"][variant]
        metrics = summary["production_stages"]["rerank"]
        lines.append(
            f"| {variant} | {metrics['recall@1']:.4f} | {metrics['recall@3']:.4f} | "
            f"{metrics['recall@5']:.4f} | {metrics['recall@10']:.4f} | "
            f"{metrics['mrr']:.4f} | {metrics['ndcg@5']:.4f} | {metrics['ndcg@10']:.4f} | "
            f"{summary['production_mean_end_to_end_ms']:.1f} | "
            f"{summary['translation_failure_rate']:.2%} |"
        )
    lines.extend(
        [
            "",
            "## Candidate diagnostics",
            "",
            "| Variant | Union R@30 | Union R@50 | Union R@100 | Dense-English R@50 | BM25-English R@50 |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for variant in VARIANTS:
        summary = result["variants"][variant]
        candidate = summary["candidate_union_recall"]
        diagnostic = summary["diagnostic_stages"]
        lines.append(
            f"| {variant} | {candidate['per_list@30']:.4f} | {candidate['per_list@50']:.4f} | "
            f"{candidate['per_list@100']:.4f} | {diagnostic['dense_english']['recall@50']:.4f} | "
            f"{diagnostic['bm25_english']['recall@50']:.4f} |"
        )
    hard = result["hard_13_miss_slice"]
    decision = result["decision"]
    lines.extend(
        [
            "",
            "## Hard 13 diagnostic slice",
            "",
            f"- Raw four-list union per-list top-30 recovered: {hard['recovered_in_candidate_union@30']}/{hard['evaluated_slice_size']}",
            f"- Raw four-list union per-list top-50 recovered: {hard['recovered_in_candidate_union@50']}/{hard['evaluated_slice_size']}",
            f"- RRF top-30 recovered: {hard['recovered_in_rrf@30']}/{hard['evaluated_slice_size']}",
            f"- RRF top-50 recovered: {hard['recovered_in_rrf@50']}/{hard['evaluated_slice_size']}",
            f"- Final rerank top-10 recovered: {hard['recovered_in_final_rerank@10']}/{hard['evaluated_slice_size']}",
            "",
            "## Decision",
            "",
            f"**{decision['decision']}** — {decision['rule']}",
            "",
        ]
    )
    return "\n".join(lines)


async def evaluate(dataset: Path, *, candidate_depth: int = 100) -> dict[str, Any]:
    payload = json.loads(dataset.read_text(encoding="utf-8"))
    contract = validate_resolved_dataset(payload)
    items = dev_answerable_items(payload)
    if not items:
        raise ValueError("No answerable dev cases")
    if any(item.get("split") != "dev" for item in items):
        raise ValueError("Cross-language A/B may evaluate dev only")
    if candidate_depth < max(DEPTHS):
        raise ValueError(f"candidate_depth must be at least {max(DEPTHS)}")

    settings = get_settings()
    if (
        settings.env == "test"
        or settings.embedding_model == "fake"
        or settings.rerank_model == "fake"
        or settings.llm_model == "fake"
    ):
        raise ValueError("Real evaluation requires real embedding, rerank, and LLM models")
    if not settings.embedding_revision or not settings.rerank_revision:
        raise ValueError("Real evaluation requires pinned retrieval model revisions")
    production_settings = settings.model_copy(update={"chat_retrieval_workflow": "single_pass"})
    diagnostic_settings = settings.model_copy(
        update={
            "chat_retrieval_workflow": "single_pass",
            "retrieval_dense_top_k": candidate_depth,
            "retrieval_bm25_top_k": candidate_depth,
            "retrieval_rrf_top_k": candidate_depth,
        }
    )

    rows: dict[str, list[dict[str, Any]]] = {variant: [] for variant in VARIANTS}
    async with session_scope() as session:
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        state = await session.get(SystemState, 1)
        if state is None or str(state.active_index_snapshot_id) != contract.index_snapshot_id:
            raise ValueError("Active snapshot does not match frozen labels")
        active_chunks = (
            await session.execute(
                select(Chunk.id, Chunk.document_id)
                .join(Document, Document.id == Chunk.document_id)
                .where(Chunk.document_version_id == Document.active_document_version_id)
            )
        ).all()
        chunk_docs = {str(chunk_id): str(document_id) for chunk_id, document_id in active_chunks}
        for item in items:
            scope = SearchScope.model_validate(item["runtime_scope"])
            if scope.type != "documents":
                raise ValueError("This benchmark requires explicit frozen document scopes")
            allowed = {str(document_id) for document_id in scope.document_ids}
            gold = set(item["snapshot_labels"]["relevant_chunk_ids"])
            if any(chunk_docs.get(chunk_id) not in allowed for chunk_id in gold):
                raise ValueError("Gold label missing from active version or outside scope")

        embedder = get_embedding_provider(settings)
        llm = get_llm_provider(settings)
        reasoning_effort = (
            None
            if settings.chat_planner_reasoning_effort == "provider_default"
            else settings.chat_planner_reasoning_effort
        )
        for number, item in enumerate(items, start=1):
            query = retrieval_query(item)
            scope = SearchScope.model_validate(item["runtime_scope"])
            gold = set(item["snapshot_labels"]["relevant_chunk_ids"])
            for variant in VARIANTS:
                outcome = await build_retrieval_queries(
                    llm,
                    query,
                    mode=variant,
                    reasoning_effort=reasoning_effort,
                )
                traces: dict[str, dict[str, list[str]]] = {}
                timings: dict[str, dict[str, Any]] = {}
                for run_name, run_settings in (
                    ("production", production_settings),
                    ("diagnostic", diagnostic_settings),
                ):
                    response = await search_corpus(
                        session,
                        SearchRequest(query=query, scope=scope, top_k=10, debug=True),
                        embedder,
                        original_query=item["question"],
                        settings=run_settings,
                        retrieval_queries=outcome.queries,
                        translation_latency_ms=outcome.translation_ms,
                    )
                    if response.degraded_reasons:
                        raise ValueError(
                            f"Retrieval degraded in {run_name}: {response.degraded_reasons}"
                        )
                    debug = response.debug
                    if debug is None:
                        raise ValueError("Debug trace is required for evaluation")
                    traces[run_name] = {stage: _trace_chunk_ids(debug, stage) for stage in STAGES}
                    raw_timings = debug.get("timings_ms")
                    if not isinstance(raw_timings, dict):
                        raise ValueError("Debug trace is missing timings_ms")
                    timings[run_name] = raw_timings

                stage_ids = traces["diagnostic"]
                production_stage_ids = traces["production"]
                gold_groups = [
                    [str(chunk_id) for chunk_id in group["resolved_chunk_ids"]]
                    for group in resolved_groups(item)
                ]
                rows[variant].append(
                    {
                        "id": item["id"],
                        "question_type": item["question_type"],
                        "retrieval_queries": outcome.queries.executed(),
                        "degraded_reasons": [
                            *outcome.degraded_reasons,
                            *response.degraded_reasons,
                        ],
                        "gold_chunk_ids": sorted(gold),
                        "gold_evidence_groups": gold_groups,
                        "stage_chunk_ids": stage_ids,
                        "production_stage_chunk_ids": production_stage_ids,
                        "gold_ranks": {
                            stage: _rank_map(gold, chunk_ids)
                            for stage, chunk_ids in stage_ids.items()
                        },
                        "production_gold_ranks": {
                            stage: _rank_map(gold, chunk_ids)
                            for stage, chunk_ids in production_stage_ids.items()
                        },
                        "production_timings_ms": timings["production"],
                        "diagnostic_timings_ms": timings["diagnostic"],
                    }
                )
            print(f"dev {number}/{len(items)} {item['id']}", flush=True)

    summaries = {variant: summarize_variant(items, values) for variant, values in rows.items()}
    gold_rank_rows = {
        variant: [
            {
                "question_id": row["id"],
                "gold_chunk_id": gold_chunk_id,
                **{f"{stage}_rank": row["gold_ranks"][stage][gold_chunk_id] for stage in STAGES},
            }
            for row in values
            for gold_chunk_id in row["gold_chunk_ids"]
        ]
        for variant, values in rows.items()
    }
    return {
        "schema_version": 1,
        "split": "dev",
        "test_evaluated": False,
        "release_passed": False,
        "answer_generation_run": False,
        "citation_metrics": "not_run_retrieval_only_experiment",
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "lock_sha256": hashlib.sha256(Path("uv.lock").read_bytes()).hexdigest(),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "working_diff_sha256": hashlib.sha256(
            subprocess.check_output(["git", "diff", "--", "app", "eval"])
        ).hexdigest(),
        "snapshot_id": contract.index_snapshot_id,
        "n": len(items),
        "production_depths": {
            "dense": settings.retrieval_dense_top_k,
            "bm25": settings.retrieval_bm25_top_k,
            "rrf": settings.retrieval_rrf_top_k,
        },
        "candidate_depth_per_list": candidate_depth,
        "query_policy": "observed_user_history_plus_question; optional_cjk_english_view",
        "scope_policy": "frozen_runtime_scope_identical_between_variants",
        "embedding_signature": embedder.manifest.signature,
        "reranker": {
            "model": settings.rerank_model,
            "revision": settings.rerank_revision,
            "max_tokens": settings.rerank_max_tokens,
        },
        "translation_model": settings.llm_model,
        "variants": summaries,
        "candidate_miss_recovery": candidate_recovery(rows),
        "hard_13_miss_slice": hard_miss_comparison(rows),
        "decision": decide(summaries),
        "gold_rank_rows": gold_rank_rows,
        "questions": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidate-depth", type=int, default=100)
    parser.add_argument("--allow-local-models", action="store_true")
    parser.add_argument("--allow-llm-calls", action="store_true")
    args = parser.parse_args()
    if not args.allow_local_models:
        parser.error("Use --allow-local-models to run local retrieval models")
    if not args.allow_llm_calls:
        parser.error("Use --allow-llm-calls to send dev queries to the configured LLM")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    output = args.output_dir / "dev-results.json"
    if output.exists():
        parser.error("Output already exists; choose a new experiment directory")

    async def run() -> dict[str, Any]:
        try:
            return await evaluate(args.dataset, candidate_depth=args.candidate_depth)
        finally:
            await dispose_engine()

    try:
        result = asyncio.run(run())
    except (
        OSError,
        SQLAlchemyError,
        ValueError,
        EmbeddingError,
        RerankError,
        LLMError,
    ) as exc:
        print(f"CROSS_LANGUAGE_EVAL_FAILED: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(2) from None
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    (args.output_dir / "dev-report.md").write_text(_markdown(result), encoding="utf-8")
    print(json.dumps({"variants": result["variants"], "decision": result["decision"]}, indent=2))


if __name__ == "__main__":
    main()

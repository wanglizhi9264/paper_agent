"""Staged dev-only ranking A/B for the frozen CJK dual-query experiment."""

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
from app.services.retrieval_queries import RetrievalQueries, build_retrieval_queries
from eval.cross_language_retrieval import HARD_MISS_IDS, _trace_chunk_ids, dev_answerable_items
from eval.evidence_selection import retrieval_query
from eval.gold_evidence_metrics import (
    evaluate_gold_predictions,
    group_recall_at_k,
    resolved_groups,
)
from eval.pdf_v2_release import validate_resolved_dataset

TRACE_STAGES = (
    "dense_original",
    "dense_english",
    "bm25_original",
    "bm25_english",
    "dense_merged",
    "bm25_merged",
    "fused",
    "rerank_input",
    "rerank",
    "selected",
)


def _hit(item: dict[str, Any], retrieved: list[str], depth: int | None = None) -> bool:
    values = retrieved if depth is None else retrieved[:depth]
    return any(
        {str(value) for value in group["resolved_chunk_ids"]}.issubset(values)
        for group in resolved_groups(item)
    )


def _rank_map(gold: set[str], ranked: list[str]) -> dict[str, int | None]:
    positions = {chunk_id: rank for rank, chunk_id in enumerate(ranked, start=1)}
    return {chunk_id: positions.get(chunk_id) for chunk_id in sorted(gold)}


def _raw_union(row: dict[str, Any], depth: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for stage in ("dense_original", "dense_english", "bm25_original", "bm25_english"):
        for chunk_id in row["traces"][stage][:depth]:
            if chunk_id not in seen:
                seen.add(chunk_id)
                result.append(chunk_id)
    return result


def _mean_group_recall(
    items: list[dict[str, Any]], rows: list[dict[str, Any]], stage: str, k: int
) -> float:
    return statistics.fmean(
        group_recall_at_k(resolved_groups(item), row["traces"][stage], k)
        for item, row in zip(items, rows, strict=True)
    )


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
    raw_recall = {
        f"recall@{depth}": statistics.fmean(
            group_recall_at_k(resolved_groups(item), _raw_union(row, depth), 10_000)
            for item, row in zip(items, rows, strict=True)
        )
        for depth in (30, 50, 100)
    }
    categories = Counter(row["failure_category"] for row in rows)
    return {
        **{key: value for key, value in metrics.items() if not key.startswith("citation_")},
        "raw_union": raw_recall,
        "fused_recall@10": _mean_group_recall(items, rows, "fused", 10),
        "fused_recall@30": _mean_group_recall(items, rows, "fused", 30),
        "fused_recall@50": _mean_group_recall(items, rows, "fused", 50),
        "rescued_candidate_recall": _mean_group_recall(items, rows, "rerank_input", 10_000),
        "mean_candidate_pool_size": statistics.fmean(
            len(row["traces"]["rerank_input"]) for row in rows
        ),
        "hard_13_final_hits": sum(
            row["id"] in HARD_MISS_IDS and _hit(item, row["traces"]["selected"], 10)
            for item, row in zip(items, rows, strict=True)
        ),
        "failure_categories": dict(sorted(categories.items())),
        "mean_total_latency_ms": statistics.fmean(
            float(row["timings_ms"]["total"]) + float(row["timings_ms"].get("translation", 0.0))
            for row in rows
        ),
        "mean_rerank_latency_ms": statistics.fmean(
            float(row["timings_ms"]["rerank"]) for row in rows
        ),
    }


def classify_failure(item: dict[str, Any], traces: dict[str, list[str]]) -> str:
    if not _hit(item, _raw_union({"traces": traces}, 100)):
        return "NOT_IN_RAW_UNION"
    if not _hit(item, traces["fused"]):
        return "LOST_IN_FUSION"
    if not _hit(item, traces["rerank_input"]):
        return "LOST_BEFORE_RERANK_DEPTH"
    if not _hit(item, traces["rerank"], 10):
        return "LOST_IN_RERANK"
    return "SURVIVED_TOP10"


def _winner(summaries: dict[str, dict[str, Any]], names: list[str]) -> str:
    return max(
        names,
        key=lambda name: (
            summaries[name]["recall@10"],
            summaries[name]["mrr"],
            summaries[name]["ndcg@10"],
            -summaries[name]["mean_total_latency_ms"],
        ),
    )


async def evaluate(dataset: Path) -> dict[str, Any]:
    payload = json.loads(dataset.read_text(encoding="utf-8"))
    contract = validate_resolved_dataset(payload)
    items = dev_answerable_items(payload)
    if len(items) != 37:
        raise ValueError(f"Expected 37 answerable dev cases, got {len(items)}")
    settings = get_settings()
    if settings.env == "test" or "fake" in {
        settings.embedding_model,
        settings.rerank_model,
        settings.llm_model,
    }:
        raise ValueError("Real evaluation requires real models")

    rows: dict[str, list[dict[str, Any]]] = {}
    configs: dict[str, dict[str, object]] = {}
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
        chunk_debug = {
            str(chunk.id): {
                "chunk_id": str(chunk.id),
                "document": document.title or document.filename,
                "section": chunk.section_path,
                "retrieval_content": chunk.retrieval_content[:700],
            }
            for chunk, document in chunk_rows
        }
        for item in items:
            scope = SearchScope.model_validate(item["runtime_scope"])
            allowed = {str(value) for value in scope.document_ids}
            gold = set(item["snapshot_labels"]["relevant_chunk_ids"])
            if scope.type != "documents" or any(
                chunk_docs.get(value) not in allowed for value in gold
            ):
                raise ValueError("Gold is outside frozen document scope")

        embedder = get_embedding_provider(settings)
        llm = get_llm_provider(settings)
        effort = (
            None
            if settings.chat_planner_reasoning_effort == "provider_default"
            else settings.chat_planner_reasoning_effort
        )
        plans: dict[str, tuple[RetrievalQueries, float, list[str]]] = {}
        for item in items:
            outcome = await build_retrieval_queries(
                llm,
                retrieval_query(item),
                mode="cjk_to_english_dual",
                reasoning_effort=effort,
            )
            plans[str(item["id"])] = (
                outcome.queries,
                outcome.translation_ms,
                outcome.degraded_reasons,
            )

        async def run_variant(name: str, overrides: dict[str, object]) -> None:
            variant = settings.model_copy(
                update={
                    "retrieval_dense_top_k": 100,
                    "retrieval_bm25_top_k": 100,
                    "retrieval_rrf_top_k": 100,
                    "retrieval_rerank_top_k": 30,
                    "retrieval_candidate_rescue": "off",
                    "retrieval_rerank_query": "original",
                    **overrides,
                }
            )
            configs[name] = overrides
            values: list[dict[str, Any]] = []
            for number, item in enumerate(items, start=1):
                plan, translation_ms, degraded = plans[str(item["id"])]
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
                if degraded or response.degraded_reasons:
                    raise ValueError(f"{name}/{item['id']} degraded")
                debug = response.debug
                if debug is None:
                    raise ValueError("Missing retrieval debug")
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
                            stage: _rank_map(gold, ids) for stage, ids in traces.items()
                        },
                        "failure_category": classify_failure(item, traces),
                        "timings_ms": debug["timings_ms"],
                        "rerank_query": debug["rerank_query"],
                        "rerank_rows": debug["rerank"],
                        "rerank_input_rows": debug["rerank_input"],
                        "candidate_chunk_ids": debug["candidate_chunk_ids"],
                        "selected_ids": traces["selected"],
                    }
                )
                print(f"{name} {number}/37 {item['id']}", flush=True)
            rows[name] = values

        await run_variant(
            "P0_dual_baseline",
            {
                "retrieval_dense_top_k": 30,
                "retrieval_bm25_top_k": 30,
                "retrieval_rrf_top_k": 30,
                "retrieval_rerank_top_k": 30,
                "retrieval_cross_language_fusion": "four_way_rrf",
            },
        )
        await run_variant("A0_four_way", {"retrieval_cross_language_fusion": "four_way_rrf"})
        await run_variant(
            "A1_best_rank", {"retrieval_cross_language_fusion": "retriever_best_rank_rrf"}
        )
        summaries = {name: summarize(items, values) for name, values in rows.items()}
        best_fusion = _winner(summaries, ["A0_four_way", "A1_best_rank"])
        fusion_mode = "retriever_best_rank_rrf" if best_fusion == "A1_best_rank" else "four_way_rrf"

        await run_variant(
            "B1_rescue_5",
            {
                "retrieval_cross_language_fusion": fusion_mode,
                "retrieval_candidate_rescue": "retriever_top_m",
                "retrieval_candidate_rescue_top_m": 5,
            },
        )
        await run_variant(
            "B2_rescue_10",
            {
                "retrieval_cross_language_fusion": fusion_mode,
                "retrieval_candidate_rescue": "retriever_top_m",
                "retrieval_candidate_rescue_top_m": 10,
            },
        )
        summaries = {name: summarize(items, values) for name, values in rows.items()}
        best_rescue = _winner(summaries, [best_fusion, "B1_rescue_5", "B2_rescue_10"])
        rescue_overrides = dict(configs[best_rescue])

        depth_names = []
        for depth in (50, 80, 100):
            name = f"C_rerank_{depth}"
            await run_variant(name, {**rescue_overrides, "retrieval_rerank_top_k": depth})
            depth_names.append(name)
        summaries = {name: summarize(items, values) for name, values in rows.items()}
        best_depth = _winner(summaries, [best_rescue, *depth_names])

        await run_variant(
            "D_english_rerank",
            {**configs[best_depth], "retrieval_rerank_query": "english_if_available"},
        )
        summaries = {name: summarize(items, values) for name, values in rows.items()}
        best_final = _winner(summaries, [best_depth, "D_english_rerank"])

    by_id = {str(item["id"]): item for item in items}
    pairwise: list[dict[str, Any]] = []
    for row in rows[best_final]:
        if row["failure_category"] != "LOST_IN_RERANK":
            continue
        item = by_id[str(row["id"])]
        gold_ids = {
            str(value) for group in resolved_groups(item) for value in group["resolved_chunk_ids"]
        }
        mapping = row["candidate_chunk_ids"]
        pre_ids = row["traces"]["rerank_input"]
        post_ids = row["traces"]["rerank"]
        scores = {str(mapping[str(candidate[0])]): candidate[1] for candidate in row["rerank_rows"]}
        gold_id = next((value for value in post_ids if value in gold_ids), None)
        false_id = next((value for value in post_ids[:10] if value not in gold_ids), None)
        if gold_id is None or false_id is None:
            continue

        def describe(
            chunk_id: str,
            *,
            score_map: dict[str, float] = scores,
            pre_ranked: list[str] = pre_ids,
            post_ranked: list[str] = post_ids,
        ) -> dict[str, Any]:
            return {
                **chunk_debug[chunk_id],
                "rerank_score": score_map.get(chunk_id),
                "pre_rerank_rank": (
                    pre_ranked.index(chunk_id) + 1 if chunk_id in pre_ranked else None
                ),
                "post_rerank_rank": (
                    post_ranked.index(chunk_id) + 1 if chunk_id in post_ranked else None
                ),
            }

        pairwise.append(
            {
                "question_id": row["id"],
                "question": item["question"],
                "rerank_query": row["rerank_query"],
                "gold": describe(gold_id),
                "top_false_positive": describe(false_id),
            }
        )

    summaries = {name: summarize(items, values) for name, values in rows.items()}
    baseline = summaries["P0_dual_baseline"]
    best = summaries[best_final]
    decision = (
        "RECOMMEND NEXT CANDIDATE FOR FROZEN-TEST GATE"
        if best["recall@10"] > baseline["recall@10"] and best["mrr"] >= baseline["mrr"] - 0.01
        else "KEEP CURRENT DEFAULT"
    )
    return {
        "schema_version": 1,
        "split": "dev",
        "test_evaluated": False,
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "working_diff_sha256": hashlib.sha256(
            subprocess.check_output(["git", "diff", "--", "app", "eval"])
        ).hexdigest(),
        "snapshot_id": contract.index_snapshot_id,
        "selection": {
            "best_fusion": best_fusion,
            "best_rescue": best_rescue,
            "best_depth": best_depth,
            "best_final": best_final,
        },
        "configs": configs,
        "summaries": summaries,
        "questions": rows,
        "pairwise_rerank_debug": pairwise,
        "decision": decision,
    }


def _markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Ranking Pipeline Dev A/B",
        "",
        "Frozen test was not evaluated.",
        "",
        "| Variant | R@1 | R@3 | R@5 | R@10 | MRR | nDCG@10 | Raw@30/50/100 | Fused@10/30/50 | Pool | Hard13 | Total ms | Rerank ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for name, value in result["summaries"].items():
        lines.append(
            f"| {name} | {value['recall@1']:.4f} | {value['recall@3']:.4f} | "
            f"{value['recall@5']:.4f} | {value['recall@10']:.4f} | {value['mrr']:.4f} | "
            f"{value['ndcg@10']:.4f} | {value['raw_union']['recall@30']:.4f}/"
            f"{value['raw_union']['recall@50']:.4f}/{value['raw_union']['recall@100']:.4f} | "
            f"{value['fused_recall@10']:.4f}/{value['fused_recall@30']:.4f}/"
            f"{value['fused_recall@50']:.4f} | {value['mean_candidate_pool_size']:.1f} | "
            f"{value['hard_13_final_hits']}/13 | {value['mean_total_latency_ms']:.1f} | "
            f"{value['mean_rerank_latency_ms']:.1f} |"
        )
    lines.extend(("", "## Failure stages", ""))
    for name, value in result["summaries"].items():
        categories = value["failure_categories"]
        lines.append(
            f"- {name}: raw miss {categories.get('NOT_IN_RAW_UNION', 0)}, "
            f"fusion loss {categories.get('LOST_IN_FUSION', 0)}, "
            f"depth loss {categories.get('LOST_BEFORE_RERANK_DEPTH', 0)}, "
            f"rerank loss {categories.get('LOST_IN_RERANK', 0)}, "
            f"Top-10 hit {categories.get('SURVIVED_TOP10', 0)}."
        )
    lines.extend(("", f"Decision: **{result['decision']}**", ""))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-local-models", action="store_true")
    parser.add_argument("--allow-llm-calls", action="store_true")
    args = parser.parse_args()
    if not args.allow_local_models or not args.allow_llm_calls:
        parser.error("Both --allow-local-models and --allow-llm-calls are required")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    output = args.output_dir / "dev-results.json"
    if output.exists():
        parser.error("Output exists; choose a new directory")

    async def run() -> dict[str, Any]:
        try:
            return await evaluate(args.dataset)
        finally:
            await dispose_engine()

    try:
        result = asyncio.run(run())
    except (OSError, SQLAlchemyError, ValueError, EmbeddingError, RerankError, LLMError) as exc:
        print(f"RANKING_EVAL_FAILED: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(2) from None
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "dev-report.md").write_text(_markdown(result), encoding="utf-8")
    print(
        json.dumps(
            {
                "selection": result["selection"],
                "summaries": result["summaries"],
                "decision": result["decision"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

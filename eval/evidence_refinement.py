"""Dev-only single-pass versus bounded-refinement retrieval A/B.

The runner uses the production evidence workflow against one frozen snapshot.
It never evaluates the held-out test split, generates answers, or writes to the DB.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import AppError
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
from app.schemas.rewrite import StructuredRewrite
from app.schemas.search import SearchScope
from app.services.evidence_workflow import gather_chat_evidence
from eval.evidence_selection import retrieval_query
from eval.pdf_v2_release import validate_resolved_dataset

WORKFLOWS = ("single_pass", "bounded_refinement")


def score_retrieval(gold: set[str], retrieved: list[str]) -> dict[str, float]:
    if not gold:
        raise ValueError("Cannot score unresolved labels")
    ranks = [rank for rank, chunk_id in enumerate(retrieved[:10], start=1) if chunk_id in gold]
    return {
        "recall@10": len(gold & set(retrieved[:10])) / len(gold),
        "mrr": 1.0 / min(ranks) if ranks else 0.0,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    if not rows:
        raise ValueError("Cannot summarize an empty evaluation")
    return {
        "n": len(rows),
        "recall@10": sum(float(row["recall@10"]) for row in rows) / len(rows),
        "mrr": sum(float(row["mrr"]) for row in rows) / len(rows),
        "mean_retrieval_calls": sum(int(row["retrieval_calls"]) for row in rows) / len(rows),
        "refinement_rate": sum(int(row["retrieval_calls"]) == 2 for row in rows) / len(rows),
    }


async def evaluate(dataset: Path) -> dict[str, Any]:
    payload = json.loads(dataset.read_text(encoding="utf-8"))
    contract = validate_resolved_dataset(payload)
    items = [item for item in payload["dataset"] if item["answerable"] and item["split"] == "dev"]
    if not items:
        raise ValueError("No answerable dev cases")

    settings = get_settings()
    if (
        settings.env == "test"
        or settings.embedding_model == "fake"
        or settings.rerank_model == "fake"
        or settings.llm_model == "fake"
    ):
        raise ValueError("Real evaluation requires real models")
    if not settings.embedding_revision or not settings.rerank_revision:
        raise ValueError("Real evaluation requires pinned retrieval model revisions")

    rows: dict[str, list[dict[str, Any]]] = {workflow: [] for workflow in WORKFLOWS}
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
        for number, item in enumerate(items, start=1):
            query = retrieval_query(item)
            rewrite = StructuredRewrite(standalone_query=query)
            scope = SearchScope.model_validate(item["runtime_scope"])
            gold = set(item["snapshot_labels"]["relevant_chunk_ids"])
            for workflow in WORKFLOWS:
                variant = settings.model_copy(update={"chat_retrieval_workflow": workflow})
                response = await gather_chat_evidence(
                    session,
                    rewrite,
                    scope,
                    embedder,
                    llm,
                    variant,
                    original_query=item["question"],
                    top_k=10,
                )
                if response.degraded_reasons:
                    raise ValueError(f"Evaluation degraded: {response.degraded_reasons}")
                retrieved = [str(result.chunk_id) for result in response.results]
                rows[workflow].append(
                    {
                        "id": item["id"],
                        "question_type": item["question_type"],
                        **score_retrieval(gold, retrieved),
                        "retrieval_calls": len(response.retrieval_queries),
                        "retrieved_chunk_ids": retrieved,
                        "gold_ranks": {
                            chunk_id: retrieved.index(chunk_id) + 1
                            if chunk_id in retrieved
                            else None
                            for chunk_id in sorted(gold)
                        },
                    }
                )
            print(f"dev {number}/{len(items)} {item['id']}", flush=True)

    return {
        "schema_version": 1,
        "split": "dev",
        "test_evaluated": False,
        "release_passed": False,
        "query_policy": "user_history_concat_v1_no_llm_rewrite",
        "created_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "lock_sha256": hashlib.sha256(Path("uv.lock").read_bytes()).hexdigest(),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "working_diff_sha256": hashlib.sha256(
            subprocess.check_output(["git", "diff", "--", "app", "eval"])
        ).hexdigest(),
        "snapshot_id": contract.index_snapshot_id,
        "embedding_signature": embedder.manifest.signature,
        "reranker": {
            "model": settings.rerank_model,
            "revision": settings.rerank_revision,
            "max_tokens": settings.rerank_max_tokens,
        },
        "planner_model": settings.llm_model,
        "planner_max_tokens": settings.chat_refinement_max_tokens,
        "workflows": {
            workflow: {"summary": summarize(values), "questions": values}
            for workflow, values in rows.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-local-models", action="store_true")
    parser.add_argument("--allow-llm-calls", action="store_true")
    args = parser.parse_args()
    if not args.allow_local_models:
        parser.error("Use --allow-local-models to explicitly run local retrieval models")
    if not args.allow_llm_calls:
        parser.error("Use --allow-llm-calls to explicitly run planner model calls")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    async def run() -> dict[str, Any]:
        try:
            return await evaluate(args.dataset)
        finally:
            await dispose_engine()

    output = args.output_dir / "dev-results.json"
    if output.exists():
        parser.error("Output already exists; choose a new experiment directory")
    try:
        result = asyncio.run(run())
    except (
        OSError,
        SQLAlchemyError,
        ValueError,
        AppError,
        EmbeddingError,
        RerankError,
        LLMError,
    ) as exc:
        print(f"REFINEMENT_EVAL_FAILED: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(2) from None
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print(
        json.dumps(
            {name: value["summary"] for name, value in result["workflows"].items()},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

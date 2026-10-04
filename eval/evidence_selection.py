"""Read-only dev A/B through the real search service; no generator or web calls.

Reports contain IDs/metrics only. Labels never enter the retriever. This is a
retrieval experiment, not a full-pipeline release or a held-out test evaluation.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
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
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.index_snapshot import SystemState
from app.rerank.base import RerankError
from app.schemas.search import SearchRequest, SearchScope
from app.services.retrieval import search_corpus
from eval.pdf_v2_release import validate_resolved_dataset

VARIANTS = {
    "legacy30": (30, 30, 30, "legacy"),
    "wide80": (100, 100, 80, "legacy"),
    "wide80_coverage": (100, 100, 80, "cell_coverage"),
}


def retrieval_query(item: dict[str, Any]) -> str:
    """Only observed user history; never benchmark answers/expected rewrite."""
    history = [m["content"] for m in item.get("conversation") or [] if m.get("role") == "user"]
    return "\n".join([*history, item["question"]])


def score_row(gold: set[str], retrieved: list[str], pool: set[str]) -> dict[str, float]:
    if not gold:
        raise ValueError("Cannot score unresolved labels")
    ranks = [rank for rank, cid in enumerate(retrieved[:10], 1) if cid in gold]
    return {
        "recall@10": len(gold & set(retrieved[:10])) / len(gold),
        "mrr": 1 / min(ranks) if ranks else 0.0,
        "candidate_recall": len(gold & pool) / len(gold),
    }


async def evaluate(dataset: Path) -> dict[str, Any]:
    payload = json.loads(dataset.read_text(encoding="utf-8"))
    contract = validate_resolved_dataset(payload)
    # Deliberately no test mode or implicit promotion to avoid test-set tuning.
    items = [i for i in payload["dataset"] if i["answerable"] and i["split"] == "dev"]
    if not items:
        raise ValueError("No answerable dev cases")
    settings = get_settings()
    if (
        settings.env == "test"
        or settings.embedding_model == "fake"
        or settings.rerank_model == "fake"
    ):
        raise ValueError("Real evaluation requires real models")
    if not settings.embedding_revision or not settings.rerank_revision:
        raise ValueError("Real evaluation requires pinned model revisions")
    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in VARIANTS}
    async with session_scope() as session:
        # Stable, non-mutating DB view. No upload/reindex/delete/session writes.
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
        chunk_docs = {str(cid): str(did) for cid, did in active_chunks}
        for item in items:
            scope = SearchScope.model_validate(item["runtime_scope"])
            if scope.type != "documents":
                raise ValueError("This benchmark requires explicit frozen document scopes")
            allowed = {str(did) for did in scope.document_ids}
            gold = set(item["snapshot_labels"]["relevant_chunk_ids"])
            if any(chunk_docs.get(cid) not in allowed for cid in gold):
                raise ValueError("Gold label missing from active version or outside scope")

        embedder = get_embedding_provider(settings)
        for number, item in enumerate(items, 1):
            for name, (dense, sparse, rrf, strategy) in VARIANTS.items():
                variant = settings.model_copy(
                    update={
                        "retrieval_dense_top_k": dense,
                        "retrieval_bm25_top_k": sparse,
                        "retrieval_rrf_top_k": rrf,
                        "retrieval_selection": strategy,
                    }
                )
                response = await search_corpus(
                    session,
                    SearchRequest(
                        query=retrieval_query(item),
                        scope=item["runtime_scope"],
                        top_k=10,
                        debug=True,
                    ),
                    embedder,
                    settings=variant,
                )
                if response.degraded_reasons:
                    raise ValueError(f"Evaluation degraded: {response.degraded_reasons}")
                debug = response.debug or {}
                mapping = debug["candidate_chunk_ids"]
                retrieved = [str(r.chunk_id) for r in response.results]
                gold = set(item["snapshot_labels"]["relevant_chunk_ids"])
                rows[name].append(
                    {
                        "id": item["id"],
                        "question_type": item["question_type"],
                        **score_row(gold, retrieved, set(mapping.values())),
                        "retrieved_chunk_ids": retrieved,
                        "gold_ranks": {
                            cid: retrieved.index(cid) + 1 if cid in retrieved else None
                            for cid in sorted(gold)
                        },
                        "trace": debug,
                    }
                )
            print(f"dev {number}/{len(items)} {item['id']}", flush=True)

    variants = {}
    for name, values in rows.items():
        reasons = Counter(
            d["reason"] for row in values for d in row["trace"]["selection_decisions"]
        )
        variants[name] = {
            "summary": {
                **{
                    key: sum(r[key] for r in values) / len(values)
                    for key in ("recall@10", "mrr", "candidate_recall")
                },
                "n": len(values),
                "suppression_counts": dict(reasons),
                "by_question_type": {
                    kind: sum(r["recall@10"] for r in values if r["question_type"] == kind)
                    / sum(r["question_type"] == kind for r in values)
                    for kind in sorted({r["question_type"] for r in values})
                },
            },
            "questions": values,
        }
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
            "batch": settings.rerank_batch_size,
        },
        "variants": variants,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--allow-local-models", action="store_true")
    args = parser.parse_args()
    if not args.allow_local_models:
        parser.error("Use --allow-local-models to explicitly run the local model experiment")
    # Must be set before lazy transformers/HF imports. Never download weights here.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"

    async def run() -> dict[str, Any]:
        try:
            return await evaluate(args.dataset)
        finally:
            await dispose_engine()

    if (args.output_dir / "dev-results.json").exists():
        parser.error("Output already exists; choose a new experiment directory")
    try:
        result = asyncio.run(run())
    except (OSError, SQLAlchemyError, ValueError, EmbeddingError, RerankError) as exc:
        # Stable CLI failure, no connection strings, private queries or traceback.
        print(f"EVIDENCE_EVAL_FAILED: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(2) from None
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "dev-results.json"
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps({k: v["summary"] for k, v in result["variants"].items()}, indent=2))


if __name__ == "__main__":
    main()

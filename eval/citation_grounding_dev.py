"""Build a private, dev-only diagnostic for citation grounding failures.

The command consumes an existing full-pipeline prediction artifact. It never runs
retrieval or generation, and it refuses to inspect non-development benchmark rows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.db.session import dispose_engine, session_scope
from app.models.chunk import Chunk
from eval.gold_evidence_metrics import group_recall_at_k, resolved_groups
from eval.pdf_v2_release import validate_resolved_dataset

SOURCE_STYLE_MARKER = re.compile(r"\[Source\s+(\d+)\]", re.IGNORECASE)


def unparsed_source_marker_indices(answer: object) -> list[int]:
    """Find human-readable markers that are invalid under the `[N]` contract."""

    return [int(value) for value in SOURCE_STYLE_MARKER.findall(str(answer or ""))]


def classify_citation_failure(
    *,
    cited: list[str],
    relevant: set[str],
    gold_group_count: int,
) -> list[str]:
    """Return deterministic, non-exclusive diagnostic labels.

    Every caller has already established that Gold is in the selected context but
    no complete acceptable evidence group was cited. Extra labels describe the
    observable citation behavior without inferring unsupported model intent.
    """

    labels = ["GOLD_IN_CONTEXT_NOT_CITED"]
    unique_cited = set(cited)
    if not unique_cited:
        labels.append("MISSING_CITATION")
        return labels
    if unique_cited.isdisjoint(relevant):
        labels.append("WRONG_CONTEXT_CITED")
    if len(unique_cited) > max(3, gold_group_count):
        labels.append("OVER_CITATION")
    return labels


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = payload.get("dataset")
    if not isinstance(values, list):
        raise ValueError("Dataset must contain a dataset list")
    return [dict(value) for value in values if isinstance(value, dict)]


def _predictions(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = payload.get("predictions")
    if not isinstance(values, list):
        raise ValueError("Predictions must contain a predictions list")
    return {
        str(value["id"]): dict(value)
        for value in values
        if isinstance(value, dict) and value.get("id") is not None
    }


async def build_diagnostic(dataset: Path, predictions: Path) -> dict[str, Any]:
    dataset_payload = json.loads(dataset.read_text(encoding="utf-8"))
    validate_resolved_dataset(dataset_payload)
    prediction_payload = json.loads(predictions.read_text(encoding="utf-8"))
    by_id = _predictions(prediction_payload)
    dev_items = [
        item
        for item in _items(dataset_payload)
        if item.get("split") == "dev" and item.get("answerable") is True
    ]
    if len(dev_items) != 37:
        raise ValueError(f"Expected exactly 37 answerable dev rows, got {len(dev_items)}")
    if any(str(item["id"]) not in by_id for item in dev_items):
        raise ValueError("Predictions are missing one or more answerable dev rows")

    misses: list[tuple[dict[str, Any], dict[str, Any], set[str], list[str], list[str]]] = []
    chunk_ids: set[str] = set()
    counts: Counter[str] = Counter()
    for item in dev_items:
        prediction = by_id[str(item["id"])]
        context = [str(value) for value in prediction.get("context_chunk_ids") or []]
        if not context:
            raise ValueError(
                "Predictions do not contain context_chunk_ids; actual generation context "
                "cannot be inferred from a separate retrieval request"
            )
        cited = [str(value) for value in prediction.get("predicted_citation_chunk_ids") or []]
        groups = resolved_groups(item)
        retrieval_hit = any(
            group_recall_at_k([group], context, len(context)) == 1.0 for group in groups
        )
        citation_hit = any(group_recall_at_k([group], cited, len(cited)) == 1.0 for group in groups)
        if not retrieval_hit:
            counts["retrieval_miss"] += 1
            continue
        if citation_hit:
            counts["retrieval_hit_citation_hit"] += 1
            continue
        counts["retrieval_hit_citation_miss"] += 1
        relevant = {str(value) for group in groups for value in group.get("resolved_chunk_ids", [])}
        misses.append((item, prediction, relevant, context, cited))
        chunk_ids.update(relevant)
        chunk_ids.update(cited)

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Chunk).where(Chunk.id.in_([UUID(value) for value in chunk_ids]))
            )
        ).scalars()
        chunks = {str(chunk.id): chunk for chunk in rows}

    label_counts: Counter[str] = Counter()
    source_style_marker_samples = 0
    samples: list[dict[str, Any]] = []
    for item, prediction, relevant, context, cited in misses:
        groups = resolved_groups(item)
        labels = classify_citation_failure(
            cited=cited,
            relevant=relevant,
            gold_group_count=len(groups),
        )
        label_counts.update(labels)
        invalid_source_markers = unparsed_source_marker_indices(prediction.get("answer"))
        if invalid_source_markers:
            source_style_marker_samples += 1
        ranks: dict[str, int | None] = {chunk_id: None for chunk_id in sorted(relevant)}
        for rank, chunk_id in enumerate(context, start=1):
            if chunk_id in ranks:
                ranks[chunk_id] = rank
        samples.append(
            {
                "question_id": item["id"],
                "question": item["question"],
                "answer": prediction.get("answer"),
                "reference_answer": item.get("reference_answer"),
                "gold_chunk_ids": sorted(relevant),
                "gold_context_rank": ranks,
                "selected_context_ids": context,
                "generated_citation_ids": cited,
                "classifications": labels,
                "unparsed_source_marker_indices": invalid_source_markers,
                "cited_chunk_content": [
                    {
                        "chunk_id": chunk_id,
                        "raw_content": chunks[chunk_id].raw_content if chunk_id in chunks else None,
                    }
                    for chunk_id in cited
                ],
                "missed_gold_content": [
                    {
                        "chunk_id": chunk_id,
                        "raw_content": chunks[chunk_id].raw_content if chunk_id in chunks else None,
                    }
                    for chunk_id in sorted(relevant - set(cited))
                ],
            }
        )

    return {
        "schema_version": 1,
        "split": "dev",
        "test_evaluated": False,
        "source_predictions": str(predictions),
        "counts": {
            "retrieval_miss": counts["retrieval_miss"],
            "retrieval_hit_citation_miss": counts["retrieval_hit_citation_miss"],
            "retrieval_hit_citation_hit": counts["retrieval_hit_citation_hit"],
        },
        "classification_semantics": {
            "labels_are_non_exclusive": True,
            "OVER_CITATION": "unique citations > max(3, acceptable Gold evidence groups)",
        },
        "classification_counts": dict(sorted(label_counts.items())),
        "unparsed_source_marker_samples": source_style_marker_samples,
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output exists; refusing to overwrite a prior diagnostic")

    async def run() -> dict[str, Any]:
        try:
            return await build_diagnostic(args.dataset, args.predictions)
        finally:
            await dispose_engine()

    result = asyncio.run(run())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps({"counts": result["counts"], "classifications": result["classification_counts"]})
    )


if __name__ == "__main__":
    main()

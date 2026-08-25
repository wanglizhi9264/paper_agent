from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from eval.pdf_v2_release import (
    ReleaseGateError,
    evaluate_predictions,
    main,
    run_predictions,
    validate_corpus_evidence,
    validate_resolved_dataset,
)


def _dataset() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(60):
        answerable = index < 52
        rows.append(
            {
                "id": f"eval-{index + 1:03d}",
                "question": f"question {index}",
                "answerable": answerable,
                "split": "dev" if index % 2 == 0 else "test",
                "scope": {"type": "all"},
                "snapshot_labels": {
                    "index_snapshot_id": "snapshot-1",
                    "relevant_chunk_ids": [f"chunk-{index}"] if answerable else [],
                },
                "required_citation_chunk_ids": [f"chunk-{index}"] if answerable else [],
            }
        )
    return rows


def test_label_freeze_requires_60_questions_and_52_resolved_answerable() -> None:
    summary = validate_resolved_dataset({"dataset": _dataset()})
    assert summary.total == 60
    assert summary.answerable == 52
    assert summary.resolved_answerable == 52
    assert summary.index_snapshot_id == "snapshot-1"


def test_label_freeze_fails_before_prediction_if_an_answerable_label_is_missing() -> None:
    rows = _dataset()
    rows[3]["snapshot_labels"]["relevant_chunk_ids"] = []
    with pytest.raises(ReleaseGateError, match="LABEL_UNRESOLVED"):
        validate_resolved_dataset(rows)


def test_prediction_runner_is_deterministic_and_requires_one_result_per_question() -> None:
    rows = _dataset()

    def fake(item: dict[str, Any]) -> dict[str, Any]:
        relevant = item["snapshot_labels"]["relevant_chunk_ids"]
        return {
            "retrieved_chunk_ids": relevant,
            "predicted_citation_chunk_ids": relevant,
            "answer": "answer" if item["answerable"] else "insufficient evidence",
            "rejected_unanswerable": not item["answerable"],
            "latency_ms": 1.0,
        }

    first = run_predictions(rows, fake)
    second = run_predictions(rows, fake)
    assert first == second
    assert len(first) == 60


def test_release_metrics_apply_all_four_candidate_thresholds() -> None:
    rows = _dataset()
    predictions = []
    for item in rows:
        relevant = item["snapshot_labels"]["relevant_chunk_ids"]
        predictions.append(
            {
                "id": item["id"],
                "retrieved_chunk_ids": relevant,
                "predicted_citation_chunk_ids": relevant,
                "rejected_unanswerable": not item["answerable"],
                "answer": "ok",
                "latency_ms": 1.0,
                "error_category": None,
            }
        )
    report = evaluate_predictions(rows, predictions)
    assert report["passed"] is True
    assert report["metrics"]["recall@10"] == 1.0
    assert report["metrics"]["citation_precision"] == 1.0
    assert report["metrics"]["citation_recall"] == 1.0
    assert report["metrics"]["unanswerable_rejection"] == 1.0

    for prediction in predictions[:4]:
        prediction["retrieved_chunk_ids"] = []
        prediction["predicted_citation_chunk_ids"] = ["wrong"]
    failed = evaluate_predictions(rows, predictions)
    assert failed["passed"] is False
    assert failed["gates"]["citation_precision"]["passed"] is False


def test_metrics_reject_missing_or_duplicate_prediction_ids() -> None:
    rows = _dataset()
    with pytest.raises(ReleaseGateError, match="PREDICTION_COUNT"):
        evaluate_predictions(rows, [])
    duplicate = [{"id": rows[0]["id"]}] * 60
    with pytest.raises(ReleaseGateError, match="PREDICTION_IDS"):
        evaluate_predictions(rows, duplicate)


def test_corpus_release_evidence_requires_six_ready_documents_and_all_runtime_gates() -> None:
    payload = {
        "documents": [
            {
                "sha256_match": True,
                "status": "ready",
                "page_count": 10,
                "chunk_count": 20,
            }
            for _ in range(6)
        ],
        "active_snapshot": {
            "id": "snapshot-1",
            "document_version_count": 6,
            "faiss_reloadable": True,
            "bm25_reloadable": True,
            "restart_top_k_stable": True,
        },
        "quality_gates": {
            name: True
            for name in (
                "backend",
                "frontend",
                "integration",
                "model_smoke",
                "migration",
                "atomic_activation",
                "rollback",
                "recovery",
            )
        },
        "v2_table_citation_bbox_rate": 1.0,
        "parser_manifests": [{"parser_signature": "a" * 64} for _ in range(6)],
        "model_manifest": {
            "embedding_signature": "embedding-signature",
            "reranker_revision": "reranker-sha",
            "generator_revision": "generator-sha",
        },
    }
    assert validate_corpus_evidence(payload)["passed"] is True
    payload["quality_gates"]["model_smoke"] = False
    assert validate_corpus_evidence(payload)["passed"] is False


def test_cli_requires_explicit_live_api_confirmation(tmp_path: Path) -> None:
    assert (
        main(
            [
                "--dataset",
                str(tmp_path / "dataset.json"),
                "--hard-case-evidence",
                str(tmp_path / "hard.json"),
                "--corpus-evidence",
                str(tmp_path / "corpus.json"),
                "--output",
                str(tmp_path / "output"),
            ]
        )
        == 2
    )

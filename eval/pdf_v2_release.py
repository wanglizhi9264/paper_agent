"""Fail-closed 60-question PDF Ingestion V2 release evaluation.

Private datasets and generated reports belong under ignored ``eval/private_benchmark``
or ``eval/results`` paths. This module contains contracts only; it never embeds
private data and never substitutes oracle predictions for a live run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from app.cli.pdf_v2_gate import evaluate_gate as evaluate_hard_case_gate

THRESHOLDS = {
    "recall@10": 0.85,
    "citation_precision": 0.95,
    "citation_recall": 0.85,
    "unanswerable_rejection": 0.80,
}
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class ReleaseGateError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class DatasetSummary:
    total: int
    answerable: int
    unanswerable: int
    resolved_answerable: int
    dev: int
    test: int
    labels_sha256: str
    index_snapshot_id: str


def _items(payload: object) -> list[dict[str, Any]]:
    values = payload.get("dataset") if isinstance(payload, dict) else payload
    if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
        raise ReleaseGateError("DATASET_INVALID", "dataset must be a list of objects")
    return values


def _relevant_ids(item: dict[str, Any]) -> list[str]:
    snapshot = item.get("snapshot_labels")
    values = snapshot.get("relevant_chunk_ids") if isinstance(snapshot, dict) else None
    if values is None:
        values = item.get("relevant_chunk_ids")
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _required_citations(item: dict[str, Any]) -> list[str]:
    values = item.get("required_citation_chunk_ids")
    if not isinstance(values, list) or not values:
        return _relevant_ids(item)
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def validate_resolved_dataset(payload: object) -> DatasetSummary:
    items = _items(payload)
    if len(items) != 60:
        raise ReleaseGateError("DATASET_COUNT", f"expected 60 questions, got {len(items)}")
    ids = [item.get("id") for item in items]
    if any(not isinstance(case_id, str) or not case_id for case_id in ids) or len(
        set(ids)
    ) != 60:
        raise ReleaseGateError("DATASET_IDS", "question IDs must be non-empty and unique")
    if any(type(item.get("answerable")) is not bool for item in items):
        raise ReleaseGateError(
            "ANSWERABLE_INVALID", "every question needs explicit answerable bool"
        )
    answerable = [item for item in items if item["answerable"]]
    if len(answerable) != 52:
        raise ReleaseGateError("ANSWERABLE_COUNT", f"expected 52 answerable, got {len(answerable)}")
    unresolved = [str(item["id"]) for item in answerable if not _relevant_ids(item)]
    if unresolved:
        raise ReleaseGateError("LABEL_UNRESOLVED", ",".join(unresolved))
    invalid_unanswerable = [
        str(item["id"]) for item in items if not item["answerable"] and _relevant_ids(item)
    ]
    if invalid_unanswerable:
        raise ReleaseGateError("UNANSWERABLE_HAS_LABEL", ",".join(invalid_unanswerable))
    snapshot_ids = {
        str(item["snapshot_labels"].get("index_snapshot_id") or "")
        for item in answerable
        if isinstance(item.get("snapshot_labels"), dict)
    }
    if len(snapshot_ids) != 1 or "" in snapshot_ids:
        raise ReleaseGateError(
            "LABEL_SNAPSHOT_MISMATCH", "answerable labels must use one non-empty snapshot ID"
        )
    index_snapshot_id = next(iter(snapshot_ids))
    dev = sum(item.get("split") == "dev" for item in items)
    test = sum(item.get("split") == "test" for item in items)
    if dev == 0 or test == 0 or dev + test != 60:
        raise ReleaseGateError("SPLIT_INVALID", "all questions must belong to dev or test")
    frozen = [
        {
            "id": item["id"],
            "answerable": item["answerable"],
            "split": item["split"],
            "relevant_chunk_ids": _relevant_ids(item),
            "required_citation_chunk_ids": _required_citations(item),
        }
        for item in items
    ]
    digest = hashlib.sha256(
        json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return DatasetSummary(60, 52, 8, 52, dev, test, digest, index_snapshot_id)


def run_predictions(
    dataset: object,
    predict: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    items = _items(dataset)
    validate_resolved_dataset(items)
    predictions: list[dict[str, Any]] = []
    for item in items:
        try:
            value = predict(item)
            predictions.append(
                {
                    "id": item["id"],
                    "retrieved_chunk_ids": list(value.get("retrieved_chunk_ids") or []),
                    "predicted_citation_chunk_ids": list(
                        value.get("predicted_citation_chunk_ids") or []
                    ),
                    "rejected_unanswerable": value.get("rejected_unanswerable") is True,
                    "answer": str(value.get("answer") or ""),
                    "latency_ms": float(value.get("latency_ms") or 0.0),
                    "error_category": value.get("error_category"),
                }
            )
        except Exception as exc:
            predictions.append(
                {
                    "id": item["id"],
                    "retrieved_chunk_ids": [],
                    "predicted_citation_chunk_ids": [],
                    "rejected_unanswerable": False,
                    "answer": "",
                    "latency_ms": 0.0,
                    "error_category": type(exc).__name__,
                }
            )
    return predictions


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def evaluate_predictions(dataset: object, predictions: object) -> dict[str, object]:
    items = _items(dataset)
    validate_resolved_dataset(items)
    if not isinstance(predictions, list) or len(predictions) != 60:
        raise ReleaseGateError("PREDICTION_COUNT", "exactly 60 predictions are required")
    prediction_ids = [value.get("id") for value in predictions if isinstance(value, dict)]
    item_ids = [item["id"] for item in items]
    if len(prediction_ids) != 60 or set(prediction_ids) != set(item_ids):
        raise ReleaseGateError("PREDICTION_IDS", "prediction IDs must match dataset exactly")
    if len(prediction_ids) != len(set(prediction_ids)):
        raise ReleaseGateError("PREDICTION_IDS", "prediction IDs must be unique")
    by_id = {value["id"]: value for value in predictions}
    recall_values: list[float] = []
    citation_hits = 0
    citation_predicted = 0
    citation_required = 0
    unanswerable_total = 0
    unanswerable_rejected = 0
    errors = 0
    latencies: list[float] = []
    split_hits: dict[str, list[float]] = {"dev": [], "test": []}
    for item in items:
        prediction = by_id[item["id"]]
        if prediction.get("error_category"):
            errors += 1
        latencies.append(float(prediction.get("latency_ms") or 0.0))
        if item["answerable"]:
            relevant = set(_relevant_ids(item))
            retrieved = {
                str(value) for value in (prediction.get("retrieved_chunk_ids") or [])[:10]
            }
            recall = _ratio(len(relevant & retrieved), len(relevant))
            recall_values.append(recall)
            split_hits[str(item["split"])].append(recall)
            required = set(_required_citations(item))
            cited = {
                str(value)
                for value in prediction.get("predicted_citation_chunk_ids") or []
            }
            citation_hits += len(required & cited)
            citation_predicted += len(cited)
            citation_required += len(required)
        else:
            unanswerable_total += 1
            unanswerable_rejected += prediction.get("rejected_unanswerable") is True
    metrics = {
        "recall@10": sum(recall_values) / len(recall_values),
        "citation_precision": _ratio(citation_hits, citation_predicted),
        "citation_recall": _ratio(citation_hits, citation_required),
        "unanswerable_rejection": _ratio(unanswerable_rejected, unanswerable_total),
        "dev_recall@10": sum(split_hits["dev"]) / len(split_hits["dev"]),
        "test_recall@10": sum(split_hits["test"]) / len(split_hits["test"]),
        "mean_latency_ms": sum(latencies) / len(latencies),
        "prediction_errors": errors,
    }
    gates = {
        name: {
            "value": metrics[name],
            "threshold": threshold,
            "passed": metrics[name] >= threshold,
        }
        for name, threshold in THRESHOLDS.items()
    }
    gates["prediction_errors"] = {"value": errors, "threshold": 0, "passed": errors == 0}
    return {
        "passed": all(gate["passed"] for gate in gates.values()),
        "metrics": metrics,
        "gates": gates,
    }


def validate_corpus_evidence(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ReleaseGateError("CORPUS_EVIDENCE_INVALID", "root must be an object")
    documents = payload.get("documents")
    if not isinstance(documents, list) or len(documents) != 6:
        raise ReleaseGateError("CORPUS_DOCUMENT_COUNT", "exactly six documents are required")
    document_passed = all(
        isinstance(doc, dict)
        and doc.get("sha256_match") is True
        and doc.get("status") == "ready"
        and isinstance(doc.get("page_count"), int)
        and doc["page_count"] > 0
        and isinstance(doc.get("chunk_count"), int)
        and doc["chunk_count"] > 0
        for doc in documents
    )
    snapshot = payload.get("active_snapshot")
    snapshot_passed = (
        isinstance(snapshot, dict)
        and isinstance(snapshot.get("id"), str)
        and bool(snapshot["id"])
        and snapshot.get("document_version_count") == 6
        and snapshot.get("faiss_reloadable") is True
        and snapshot.get("bm25_reloadable") is True
        and snapshot.get("restart_top_k_stable") is True
    )
    required_quality = {
        "backend",
        "frontend",
        "integration",
        "model_smoke",
        "migration",
        "atomic_activation",
        "rollback",
        "recovery",
    }
    quality = payload.get("quality_gates")
    quality_passed = isinstance(quality, dict) and all(
        quality.get(key) is True for key in required_quality
    )
    bbox_passed = payload.get("v2_table_citation_bbox_rate") == 1.0
    parser_manifests = payload.get("parser_manifests")
    parser_manifest_passed = (
        isinstance(parser_manifests, list)
        and len(parser_manifests) == 6
        and all(
            isinstance(manifest, dict)
            and isinstance(manifest.get("parser_signature"), str)
            and len(manifest["parser_signature"]) == 64
            for manifest in parser_manifests
        )
    )
    model_manifest = payload.get("model_manifest")
    model_manifest_passed = isinstance(model_manifest, dict) and all(
        isinstance(model_manifest.get(key), str) and bool(model_manifest[key])
        for key in ("embedding_signature", "reranker_revision", "generator_revision")
    )
    report = {
        "passed": (
            document_passed
            and snapshot_passed
            and quality_passed
            and bbox_passed
            and parser_manifest_passed
            and model_manifest_passed
        ),
        "documents": document_passed,
        "snapshot": snapshot_passed,
        "quality_gates": quality_passed,
        "v2_table_citation_bbox": bbox_passed,
        "parser_manifests": parser_manifest_passed,
        "model_manifest": model_manifest_passed,
    }
    return report


def _load(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseGateError("INPUT_UNAVAILABLE", f"{path.name}: {type(exc).__name__}") from exc


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def _is_rejection(answer: str, citations: list[str]) -> bool:
    normalized = answer.casefold()
    phrases = ("insufficient evidence", "not enough evidence", "证据不足", "无法从")
    return not citations and any(phrase in normalized for phrase in phrases)


class LiveAPIAdapter:
    def __init__(self, base_url: str, timeout: float) -> None:
        import httpx

        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def predict(self, item: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        scope = item["scope"]
        search = self._client.post(
            "/api/v1/search", json={"query": item["question"], "scope": scope, "top_k": 10}
        )
        search.raise_for_status()
        retrieved = [value["chunk_id"] for value in search.json()["results"]]
        created = self._client.post(
            "/api/v1/sessions", json={"title": f"eval:{item['id']}", "scope": scope}
        )
        created.raise_for_status()
        session_id = created.json()["id"]
        try:
            chat = self._client.post(
                "/api/v1/chat", json={"session_id": session_id, "query": item["question"]}
            )
            chat.raise_for_status()
            body = chat.json()
        finally:
            self._client.delete(f"/api/v1/sessions/{session_id}")
        cited = [value["chunk_id"] for value in body.get("citations", [])]
        return {
            "retrieved_chunk_ids": retrieved,
            "predicted_citation_chunk_ids": cited,
            "answer": body.get("answer", ""),
            "rejected_unanswerable": _is_rejection(body.get("answer", ""), cited),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }


def _write_report(
    output: Path,
    dataset_path: Path,
    summary: DatasetSummary,
    predictions: list[dict[str, Any]],
    metrics: dict[str, object],
    evidence: dict[str, object],
    corpus_payload: dict[str, Any],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "git_commit": _git_commit(),
        "lock_sha256": _sha256(REPOSITORY_ROOT / "uv.lock"),
        "dataset_sha256": _sha256(dataset_path),
        "label_freeze": asdict(summary),
        "active_snapshot": corpus_payload["active_snapshot"],
        "parser_manifests": corpus_payload["parser_manifests"],
        "model_manifest": corpus_payload["model_manifest"],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (output / "predictions.json").write_text(
        json.dumps({"predictions": predictions}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (output / "release-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    metric_values = metrics["metrics"]
    lines = [
        "# PDF Ingestion V2 Release Report",
        "",
        f"Status: {'PASSED' if metrics['passed'] and evidence['passed'] else 'FAILED'}",
        "",
        "| Metric | Value | Threshold | Passed |",
        "| --- | ---: | ---: | --- |",
    ]
    for name, threshold in THRESHOLDS.items():
        value = metric_values[name]
        lines.append(f"| {name} | {value:.4f} | {threshold:.2f} | {value >= threshold} |")
    (output / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--hard-case-evidence", type=Path, required=True)
    parser.add_argument("--corpus-evidence", type=Path, required=True)
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-live-api", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.allow_live_api:
        print("LIVE_API_NOT_CONFIRMED: pass --allow-live-api", file=sys.stderr)
        return 2
    try:
        dataset_payload = _load(args.dataset)
        items = _items(dataset_payload)
        summary = validate_resolved_dataset(items)
        hard_cases = evaluate_hard_case_gate(_load(args.hard_case_evidence))
        corpus_payload = _load(args.corpus_evidence)
        corpus = validate_corpus_evidence(corpus_payload)
        if (
            isinstance(corpus_payload, dict)
            and isinstance(corpus_payload.get("active_snapshot"), dict)
            and corpus_payload["active_snapshot"].get("id") != summary.index_snapshot_id
        ):
            raise ReleaseGateError(
                "LABEL_SNAPSHOT_MISMATCH", "frozen labels do not match active snapshot"
            )
        if not hard_cases["passed"] or not corpus["passed"]:
            raise ReleaseGateError("PRECONDITION_FAILED", "hard-case/corpus evidence is not green")
    except ReleaseGateError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    adapter = LiveAPIAdapter(args.api_base, args.timeout)
    try:
        predictions = run_predictions(items, adapter.predict)
    finally:
        adapter.close()
    metrics = evaluate_predictions(items, predictions)
    evidence = {
        "passed": hard_cases["passed"] and corpus["passed"],
        "hard_cases": hard_cases,
        "corpus": corpus,
    }
    if not isinstance(corpus_payload, dict):
        raise RuntimeError("validated corpus evidence changed type")
    _write_report(
        args.output,
        args.dataset,
        summary,
        predictions,
        metrics,
        evidence,
        corpus_payload,
    )
    return 0 if metrics["passed"] and evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

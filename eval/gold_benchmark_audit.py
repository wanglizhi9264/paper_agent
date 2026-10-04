"""Structural and split-leakage audit for a resolved gold-evidence benchmark.

The audit is intentionally independent of retrieval output.  It checks only the
stable source anchors and resolver-produced labels in a frozen benchmark.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from eval.gold_evidence import evidence_quote_hash, immutable_qa_digest


def _split_memberships(values: dict[str, set[str]]) -> list[dict[str, Any]]:
    return [
        {"key": key, "splits": sorted(splits)}
        for key, splits in sorted(values.items())
        if len(splits) > 1
    ]


def audit_benchmark(
    payload: dict[str, Any],
    *,
    expected_total: int | None = None,
    expected_dev: int | None = None,
    expected_test: int | None = None,
    require_leakage_free: bool = False,
) -> dict[str, Any]:
    """Return a deterministic structural and leakage report."""
    items = payload.get("dataset", [])
    errors: list[str] = []
    warnings: list[str] = []
    ids = [str(item.get("id", "")) for item in items]
    duplicate_ids = sorted(value for value, count in Counter(ids).items() if count > 1)
    if duplicate_ids:
        errors.append(f"duplicate sample ids: {duplicate_ids}")

    split_counts = Counter(str(item.get("split", "missing")) for item in items)
    type_counts = Counter(str(item.get("question_type", "missing")) for item in items)
    answerable_count = sum(bool(item.get("answerable")) for item in items)
    unanswerable_count = len(items) - answerable_count
    expected_counts = {
        "total": expected_total,
        "dev": expected_dev,
        "test": expected_test,
    }
    actual_counts = {
        "total": len(items),
        "dev": split_counts.get("dev", 0),
        "test": split_counts.get("test", 0),
    }
    for name, expected in expected_counts.items():
        if expected is not None and actual_counts[name] != expected:
            errors.append(f"expected {name}={expected}, got {actual_counts[name]}")

    anchors_by_split: dict[str, set[str]] = defaultdict(set)
    pages_by_split: dict[str, set[str]] = defaultdict(set)
    chunks_by_split: dict[str, set[str]] = defaultdict(set)
    resolution_counts: Counter[str] = Counter()

    for item in items:
        case_id = str(item.get("id", "missing"))
        split = str(item.get("split", "missing"))
        answerable = bool(item.get("answerable"))
        evidence = item.get("evidence", [])
        relevant_ids = [str(value) for value in item.get("relevant_chunk_ids", [])]
        required_ids = [str(value) for value in item.get("required_citation_chunk_ids", [])]

        if answerable:
            if item.get("reference_answer") in {None, ""}:
                errors.append(f"{case_id}: answerable sample has no reference answer")
            if not evidence:
                errors.append(f"{case_id}: answerable sample has no evidence")
            if not relevant_ids:
                errors.append(f"{case_id}: answerable sample has no resolved chunks")
            if relevant_ids != required_ids:
                errors.append(f"{case_id}: retrieval and citation labels differ")
        else:
            if item.get("reference_answer") is not None:
                errors.append(f"{case_id}: unanswerable sample has a reference answer")
            if evidence:
                errors.append(f"{case_id}: unanswerable sample has evidence")
            if relevant_ids or required_ids:
                errors.append(f"{case_id}: unanswerable sample has positive chunk labels")

        question_status = str(item.get("evidence_resolution_status", "missing"))
        resolution_counts[question_status] += 1
        if answerable and question_status not in {"resolved", "multi_chunk"}:
            errors.append(f"{case_id}: non-final evidence status {question_status}")
        if not answerable and question_status != "not_applicable":
            warnings.append(f"{case_id}: expected not_applicable status, got {question_status}")

        for chunk_id in relevant_ids:
            chunks_by_split[chunk_id].add(split)

        for evidence_index, anchor in enumerate(evidence):
            document_key = str(anchor.get("document_key", ""))
            page = str(anchor.get("page", ""))
            quote = str(anchor.get("quote", ""))
            quote_hash_version = str(anchor.get("quote_hash_version") or "legacy-v1")
            expected_hash = evidence_quote_hash(quote, quote_hash_version)
            stored_hash = str(anchor.get("quote_hash", ""))
            if stored_hash != expected_hash:
                errors.append(f"{case_id}[{evidence_index}]: stale evidence quote hash")
            anchor_key = f"{document_key}|{page}|{stored_hash}"
            page_key = f"{document_key}|{page}"
            anchors_by_split[anchor_key].add(split)
            pages_by_split[page_key].add(split)

            resolution = anchor.get("resolution", {})
            resolution_status = str(resolution.get("status", "missing"))
            if answerable and resolution_status not in {"resolved", "multi_chunk"}:
                errors.append(
                    f"{case_id}[{evidence_index}]: non-final anchor status {resolution_status}"
                )
            resolved_ids = [str(value) for value in resolution.get("resolved_chunk_ids", [])]
            if answerable and not resolved_ids:
                errors.append(f"{case_id}[{evidence_index}]: empty resolved chunk group")

    cross_split_anchors = _split_memberships(anchors_by_split)
    cross_split_pages = _split_memberships(pages_by_split)
    cross_split_chunks = _split_memberships(chunks_by_split)
    if require_leakage_free:
        if cross_split_anchors:
            errors.append(f"cross-split evidence anchors: {len(cross_split_anchors)}")
        if cross_split_pages:
            errors.append(f"cross-split source pages: {len(cross_split_pages)}")
        if cross_split_chunks:
            errors.append(f"cross-split resolved chunks: {len(cross_split_chunks)}")

    report = {
        "schema_version": "gold-benchmark-audit-v1",
        "passed": not errors,
        "dataset_version": payload.get("dataset_version"),
        "question_answer_sha256": immutable_qa_digest(payload),
        "counts": {
            "total": len(items),
            "answerable": answerable_count,
            "unanswerable": unanswerable_count,
            "splits": dict(sorted(split_counts.items())),
            "question_types": dict(sorted(type_counts.items())),
            "resolution_statuses": dict(sorted(resolution_counts.items())),
        },
        "leakage": {
            "cross_split_evidence_anchor_count": len(cross_split_anchors),
            "cross_split_source_page_count": len(cross_split_pages),
            "cross_split_resolved_chunk_count": len(cross_split_chunks),
            "cross_split_evidence_anchors": cross_split_anchors,
            "cross_split_source_pages": cross_split_pages,
            "cross_split_resolved_chunks": cross_split_chunks,
        },
        "errors": errors,
        "warnings": warnings,
    }
    return report


def render_markdown(report: dict[str, Any]) -> str:
    """Render a concise, deterministic human-review summary."""
    counts = report["counts"]
    leakage = report["leakage"]
    status = "PASS" if report["passed"] else "FAIL"
    lines = [
        "# Gold Benchmark Audit",
        "",
        f"Status: **{status}**",
        "",
        f"- Dataset version: `{report.get('dataset_version')}`",
        f"- Samples: {counts['total']} ({counts['answerable']} answerable, "
        f"{counts['unanswerable']} unanswerable)",
        f"- Splits: `{json.dumps(counts['splits'], ensure_ascii=False, sort_keys=True)}`",
        f"- Question/answer digest: `{report['question_answer_sha256']}`",
        f"- Cross-split evidence anchors: {leakage['cross_split_evidence_anchor_count']}",
        f"- Cross-split source pages: {leakage['cross_split_source_page_count']}",
        f"- Cross-split resolved chunks: {leakage['cross_split_resolved_chunk_count']}",
        "",
        "## Question types",
        "",
    ]
    lines.extend(f"- {name}: {count}" for name, count in counts["question_types"].items())
    lines.extend(["", "## Errors", ""])
    lines.extend(f"- {value}" for value in report["errors"] or ["None"])
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {value}" for value in report["warnings"] or ["None"])
    return "\n".join(lines) + "\n"


def render_review_markdown(payload: dict[str, Any], report: dict[str, Any]) -> str:
    """Render every question, answer, stable anchor, and derived label for review."""
    lines = [
        "# Gold Evidence Benchmark Review",
        "",
        "> Gold truth is the source document/page/section/quote. Chunk IDs below are derived "
        "labels for the pinned snapshot and may be regenerated.",
        "> The review sheet contains no retrieval predictions and must not be used to tune "
        "against the frozen test split.",
        "",
        "## Audit summary",
        "",
        f"- Status: **{'PASS' if report['passed'] else 'FAIL'}**",
        f"- Dataset version: `{report.get('dataset_version')}`",
        f"- Question/answer digest: `{report['question_answer_sha256']}`",
        f"- Samples: {report['counts']['total']} "
        f"({report['counts']['answerable']} answerable, "
        f"{report['counts']['unanswerable']} unanswerable)",
        f"- Cross-split anchor/page/chunk overlap: "
        f"{report['leakage']['cross_split_evidence_anchor_count']} / "
        f"{report['leakage']['cross_split_source_page_count']} / "
        f"{report['leakage']['cross_split_resolved_chunk_count']}",
        "",
        "## Questions",
        "",
    ]
    for item in payload.get("dataset", []):
        lines.extend(
            [
                f"### {item.get('id')} - {item.get('split')} - {item.get('question_type')}",
                "",
                f"**Question:** {item.get('question')}",
                "",
                f"**Reference answer:** {item.get('reference_answer') or 'Unanswerable'}",
                "",
                f"**Resolution status:** `{item.get('evidence_resolution_status')}`",
                "",
            ]
        )
        points = item.get("reference_answer_points", [])
        if points:
            lines.extend(["**Required answer points:**", ""])
            lines.extend(f"- {value}" for value in points)
            lines.append("")
        evidence = item.get("evidence", [])
        if not evidence:
            lines.extend(
                [
                    f"**Unanswerable rationale:** {item.get('notes') or 'No source evidence.'}",
                    "",
                ]
            )
            continue
        for index, anchor in enumerate(evidence, start=1):
            resolution = anchor.get("resolution", {})
            decision = anchor.get("review_decision")
            lines.extend(
                [
                    f"#### Evidence {index}",
                    "",
                    f"- Source: `{anchor.get('document_key')}`, physical page {anchor.get('page')}",
                    f"- Section: `{' / '.join(anchor.get('section_path', [])) or 'unspecified'}`",
                    f"- Quote hash: `{anchor.get('quote_hash')}`",
                    f"- Match: `{resolution.get('match_method')}`; chunks: "
                    f"`{', '.join(resolution.get('resolved_chunk_ids', []))}`",
                    "- Gold quote:",
                    "",
                    f"> {str(anchor.get('quote', '')).replace(chr(10), chr(10) + '> ')}",
                    "",
                ]
            )
            if decision:
                lines.extend(
                    [
                        f"- Source review: `{decision.get('decision')}` on "
                        f"`{decision.get('reviewed_at')}`",
                        f"- Verified text: {decision.get('source_verified_text')}",
                        f"- Review rationale: {decision.get('rationale')}",
                        "",
                    ]
                )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-markdown", type=Path)
    parser.add_argument("--output-review-markdown", type=Path)
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--expected-dev", type=int)
    parser.add_argument("--expected-test", type=int)
    parser.add_argument("--require-leakage-free", action="store_true")
    args = parser.parse_args()
    payload = json.loads(args.dataset.read_text(encoding="utf-8"))
    report = audit_benchmark(
        payload,
        expected_total=args.expected_total,
        expected_dev=args.expected_dev,
        expected_test=args.expected_test,
        require_leakage_free=args.require_leakage_free,
    )
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if args.output_markdown:
        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_markdown.write_text(render_markdown(report), encoding="utf-8")
    if args.output_review_markdown:
        args.output_review_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_review_markdown.write_text(
            render_review_markdown(payload, report), encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

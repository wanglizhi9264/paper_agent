"""Fail-closed V2-7 hard-case evidence gate for private-machine reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HARD_CASE_IDS = (
    "eval-001",
    "eval-002",
    "eval-022",
    "eval-023",
    "eval-024",
    "eval-025",
    "eval-027",
    "eval-028",
    "eval-029",
    "eval-030",
    "eval-048",
)
TABLE_CASE_IDS = frozenset(set(HARD_CASE_IDS) - {"eval-030"})


def _strings(value: object) -> set[str]:
    return {str(item) for item in value} if isinstance(value, list) else set()


def _rewrite_slots_valid(row: dict[str, Any]) -> bool:
    slots = row.get("rewrite_slots")
    if not isinstance(slots, dict):
        return False
    return (
        "EEG2IM" in _strings(slots.get("paper"))
        and "ImageNet-4" in _strings(slots.get("dataset"))
        and "H+L+FiLM" in _strings(slots.get("method"))
        and {"IS", "FID"}.issubset(_strings(slots.get("metrics")))
    )


def evaluate_gate(rows: object) -> dict[str, object]:
    if not isinstance(rows, list):
        return {"passed": False, "resolved": 0, "total": 11, "failures": ["INVALID_ROOT"]}
    by_id: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for value in rows:
        if not isinstance(value, dict) or not isinstance(value.get("case_id"), str):
            failures.append("INVALID_CASE")
            continue
        case_id = value["case_id"]
        if case_id in by_id:
            failures.append(f"{case_id}:DUPLICATE")
        by_id[case_id] = value
    extras = sorted(set(by_id) - set(HARD_CASE_IDS))
    failures.extend(f"{case_id}:UNKNOWN" for case_id in extras)
    resolved = 0
    for case_id in HARD_CASE_IDS:
        row = by_id.get(case_id)
        if row is None:
            failures.append(f"{case_id}:MISSING")
            continue
        checks = {
            "EVIDENCE": row.get("evidence_resolvable") is True,
            "BINDING": row.get("binding_correct") is True,
            "PHYSICAL_PAGE": row.get("physical_page_correct") is True,
        }
        if case_id in TABLE_CASE_IDS:
            checks["TABLE_BBOX"] = row.get("table_bbox_correct") is True
        if case_id == "eval-048":
            checks["REWRITE_SLOTS"] = _rewrite_slots_valid(row)
        case_failures = [name for name, passed in checks.items() if not passed]
        failures.extend(f"{case_id}:{name}" for name in case_failures)
        if not case_failures:
            resolved += 1
    return {
        "passed": not failures and resolved == len(HARD_CASE_IDS),
        "resolved": resolved,
        "total": len(HARD_CASE_IDS),
        "failures": failures,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        rows = json.loads(args.evidence.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"passed": False, "error": type(exc).__name__}), file=sys.stderr)
        return 2
    report = evaluate_gate(rows)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

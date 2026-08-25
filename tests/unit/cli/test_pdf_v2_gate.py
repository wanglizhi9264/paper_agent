from __future__ import annotations

import json
from pathlib import Path

from app.cli.pdf_v2_gate import HARD_CASE_IDS, TABLE_CASE_IDS, evaluate_gate, main


def _passing_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for case_id in HARD_CASE_IDS:
        row: dict[str, object] = {
            "case_id": case_id,
            "evidence_resolvable": True,
            "binding_correct": True,
            "physical_page_correct": True,
            "table_bbox_correct": True if case_id in TABLE_CASE_IDS else None,
        }
        if case_id == "eval-048":
            row["rewrite_slots"] = {
                "paper": ["EEG2IM"],
                "dataset": ["ImageNet-4"],
                "method": ["H+L+FiLM"],
                "metrics": ["IS", "FID"],
            }
        rows.append(row)
    return rows


def test_gate_accepts_exactly_eleven_resolved_cases() -> None:
    report = evaluate_gate(_passing_rows())
    assert report["passed"] is True
    assert report["resolved"] == 11
    assert report["total"] == 11


def test_gate_rejects_missing_case_and_bad_eval_048_slot() -> None:
    rows = _passing_rows()[:-1]
    assert evaluate_gate(rows)["passed"] is False

    rows = _passing_rows()
    rows[-1]["rewrite_slots"] = {"paper": ["EEG2IM"]}
    report = evaluate_gate(rows)
    assert report["passed"] is False
    assert "eval-048:REWRITE_SLOTS" in report["failures"]


def test_cli_returns_nonzero_for_failed_private_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(_passing_rows()[:-1]), encoding="utf-8")
    assert main(["--evidence", str(evidence)]) == 1
    assert main(["--evidence", str(tmp_path / "missing.json")]) == 2

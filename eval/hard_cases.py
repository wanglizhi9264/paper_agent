"""11 Hard Cases for PDF Ingestion V2-0 baseline (spec §17).

This module defines the 11 hard cases from the private benchmark and
provides a baseline runner that diagnoses each case with the current
PyMuPDF V1 parser.

V2-0 does NOT fix these cases — it only reports the current failure state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from eval.pdf_baseline import (
    BaselineReport,
    diagnose_pdf,
)


@dataclass
class HardCase:
    """A single hard case from the private benchmark (spec §17)."""

    case_id: str
    description: str
    paper_hint: str
    expected_structure: str
    expected_text_anchors: list[str]
    expected_binding: dict[str, list[str]] = field(default_factory=dict)
    failure_layer: str = ""  # "table_structure", "unicode", "paragraph", etc.
    known_v1_status: str = "FAIL"  # V1 known to fail this case


# The 11 hard cases from spec §17.
HARD_CASES: list[HardCase] = [
    HardCase(
        case_id="eval-001",
        description="DDPM row 'Ours (Lsimple)' binding IS 9.46±0.11, FID 3.17",
        paper_hint="DDPM",
        expected_structure="table_row + header_binding",
        expected_text_anchors=["9.46±0.11", "3.17", "Lsimple"],
        expected_binding={
            "IS": ["9.46±0.11"],
            "FID": ["3.17"],
        },
        failure_layer="table_structure + unicode_normalization",
        known_v1_status="FAIL",
    ),
    HardCase(
        case_id="eval-002",
        description="EEG2IM row/header binding Accuracy/F1 and IS/FID",
        paper_hint="EEG2IM",
        expected_structure="table_row + multi_metric_binding",
        expected_text_anchors=["EEG2IM", "Accuracy", "F1", "IS", "FID"],
        expected_binding={
            "Accuracy": [],
            "F1": [],
            "IS": [],
            "FID": [],
        },
        failure_layer="table_row_reconstruction",
        known_v1_status="FAIL",
    ),
    HardCase(
        case_id="eval-022",
        description="DDPM Table 2 two objective rows each binding FID",
        paper_hint="DDPM",
        expected_structure="table_row + row_binding",
        expected_text_anchors=["Lsimple", "Lhybrid", "Lvlb"],
        expected_binding={
            "Lsimple_FID": [],
            "Lhybrid_FID": [],
            "Lvlb_FID": [],
        },
        failure_layer="table_row_reconstruction",
        known_v1_status="FAIL",
    ),
    HardCase(
        case_id="eval-023",
        description="ImageNet-40 H, H+L+FiLM binding IS/FID",
        paper_hint="EEG2IM",
        expected_structure="multi_header + row_binding",
        expected_text_anchors=["H", "H+L+FiLM", "IS", "FID", "ImageNet-40"],
        expected_binding={
            "H_IS": [],
            "H_FID": [],
            "H+L+FiLM_IS": [],
            "H+L+FiLM_FID": [],
        },
        failure_layer="multi_header + row_binding",
        known_v1_status="FAIL",
    ),
    HardCase(
        case_id="eval-024",
        description="ImageNet-4 T, T+F+KD binding Accuracy/F1",
        paper_hint="EEG2IM",
        expected_structure="multi_header + row_binding",
        expected_text_anchors=["T", "T+F+KD", "Accuracy", "F1", "ImageNet-4"],
        expected_binding={
            "T_Accuracy": [],
            "T_F1": [],
            "T+F+KD_Accuracy": [],
            "T+F+KD_F1": [],
        },
        failure_layer="multi_header + row_binding",
        known_v1_status="FAIL",
    ),
    HardCase(
        case_id="eval-025",
        description="LMM-Large in HumanAct12/UESTC two group headers binding FID/Accuracy",
        paper_hint="LMM",
        expected_structure="multi_header_table",
        expected_text_anchors=["LMM-Large", "HumanAct12", "UESTC", "FID", "Accuracy"],
        expected_binding={
            "LMM-Large_HumanAct12_FID": [],
            "LMM-Large_UESTC_Accuracy": [],
        },
        failure_layer="multi_header_table",
        known_v1_status="FAIL",
    ),
    HardCase(
        case_id="eval-027",
        description="ACTOR UESTC Transformer/autoregressive decoder binding FIDtest/Accuracy",
        paper_hint="ACTOR",
        expected_structure="multi_header_table",
        expected_text_anchors=["ACTOR", "UESTC", "Transformer", "FIDtest", "Accuracy"],
        expected_binding={
            "ACTOR_UESTC_FIDtest": [],
            "ACTOR_UESTC_Accuracy": [],
        },
        failure_layer="multi_header_table",
        known_v1_status="FAIL",
    ),
    HardCase(
        case_id="eval-028",
        description="ACTOR/Action2Motion in HumanAct12 binding FIDtr/Accuracy",
        paper_hint="ACTOR",
        expected_structure="row_structure",
        expected_text_anchors=["ACTOR", "Action2Motion", "HumanAct12", "FIDtr", "Accuracy"],
        expected_binding={
            "ACTOR_HumanAct12_FIDtr": [],
            "Action2Motion_HumanAct12_Accuracy": [],
        },
        failure_layer="row_structure",
        known_v1_status="FAIL",
    ),
    HardCase(
        case_id="eval-029",
        description="Motion Intent model columns binding Accuracy/Sensitivity",
        paper_hint="Motion Intent",
        expected_structure="table_structure",
        expected_text_anchors=["Accuracy", "Sensitivity"],
        expected_binding={
            "model_column_Accuracy": [],
            "model_column_Sensitivity": [],
        },
        failure_layer="table_structure",
        known_v1_status="FAIL",
    ),
    HardCase(
        case_id="eval-030",
        description="Two methods respectively binding 13.61 min, 13.09 min",
        paper_hint="multiple",
        expected_structure="paragraph_reconstruction",
        expected_text_anchors=["13.61", "13.09", "min"],
        expected_binding={
            "method1_time": ["13.61"],
            "method2_time": ["13.09"],
        },
        failure_layer="paragraph_reconstruction",
        known_v1_status="FAIL",
    ),
    HardCase(
        case_id="eval-048",
        description="rewrite preserving paper/dataset/method/metrics four semantic slots",
        paper_hint="EEG2IM",
        expected_structure="query_rewrite + table_retrieval",
        expected_text_anchors=["FiLM", "IS", "FID"],
        expected_binding={
            "paper": ["EEG2IM"],
            "dataset": ["ImageNet-4"],
            "method": ["H+L+FiLM"],
            "metrics": ["IS", "FID"],
        },
        failure_layer="conversational_retrieval",
        known_v1_status="FAIL",
    ),
]


def run_hard_cases_baseline(
    benchmark_dir: Path,
) -> list[BaselineReport]:
    """Run baseline diagnostic on 11 hard cases using private benchmark.

    Args:
        benchmark_dir: Directory containing private PDFs and dataset.json.

    Returns:
        List of BaselineReport, one per hard case.
    """
    reports: list[BaselineReport] = []

    # Load benchmark dataset if available
    pdfs_dir = benchmark_dir / "pdfs"

    for case in HARD_CASES:
        # Try to find the corresponding PDF
        pdf_path: Path | None = None
        if pdfs_dir.exists():
            # Try common naming patterns
            candidates = list(pdfs_dir.glob(f"*{case.paper_hint}*.pdf")) + list(
                pdfs_dir.glob(f"*{case.paper_hint.lower()}*.pdf")
            )
            if candidates:
                pdf_path = candidates[0]

        if pdf_path is not None and pdf_path.exists():
            report = diagnose_pdf(
                pdf_path,
                expected_anchors=case.expected_text_anchors,
                expected_binding=case.expected_binding,
            )
            report.pdf_name = f"{case.case_id} ({case.paper_hint})"
        else:
            # Private data not available — record as skipped with known V1 status
            report = BaselineReport(
                pdf_name=f"{case.case_id} ({case.paper_hint})",
                pdf_path="(not available)",
                parser_id="pymupdf",
                parser_version="(not run)",
                parser_signature="(not run)",
                elapsed_ms=0,
                error_code="PRIVATE_DATA_UNAVAILABLE",
                error_message=(
                    f"Private benchmark PDF for {case.paper_hint} not found. "
                    f"V1 known status: {case.known_v1_status}. "
                    f"Failure layer: {case.failure_layer}."
                ),
                error_classification="PRIVATE_DATA_UNAVAILABLE",
                warnings=[f"case: {case.description}", f"layer: {case.failure_layer}"],
            )
        reports.append(report)

    return reports


def format_hard_cases_markdown(reports: list[BaselineReport]) -> str:
    """Format hard cases baseline as human-readable Markdown."""
    import time

    lines = [
        "# PDF Ingestion V2-0 Hard Cases Baseline",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Total Hard Cases: {len(reports)}",
        "",
        "## Summary",
        "",
        "| Case ID | Parse OK | Anchors Found | Anchors Missing | Header Binding | Error |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    for r in reports:
        parse_ok = "✓" if r.error_code is None else "✗"
        anchors_found = len(r.text_anchors_found)
        anchors_missing = len(r.text_anchors_missing)
        header_binding = (
            str(r.header_binding_correct) if r.header_binding_correct is not None else "N/A"
        )
        error = r.error_code or "none"
        lines.append(
            f"| {r.pdf_name} "
            f"| {parse_ok} "
            f"| {anchors_found} "
            f"| {anchors_missing} "
            f"| {header_binding} "
            f"| {error} |"
        )

    lines.extend(["", "## Known V1 Failure Classification", ""])
    lines.append("| Case ID | Failure Layer | V1 Status |")
    lines.append("| --- | --- | --- |")
    for case in HARD_CASES:
        lines.append(f"| {case.case_id} | {case.failure_layer} | {case.known_v1_status} |")

    lines.extend(
        [
            "",
            "## Evidence Resolution Summary",
            "",
            "- Total answerable questions: 52",
            "- V1 resolved (known from prior run): 41/52",
            "- V1 unresolved: 11/52",
            "- Unresolved cases: the 11 hard cases listed above",
            "",
            "## Notes",
            "",
            "- V2-0 does NOT fix any hard cases. It only establishes the baseline.",
            "- Private PDFs and benchmark data are stored in git-ignored directories.",
            "- The 41/52 result was reproduced from the prior benchmark run (memory.md).",
        ]
    )

    return "\n".join(lines)

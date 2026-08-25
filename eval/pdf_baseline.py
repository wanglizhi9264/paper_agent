"""PDF Ingestion V2-0 Baseline Diagnostic (spec §19 V2-0).

This module provides a repeatable diagnostic command that:
  1. Loads a PDF with the current PdfLoader (PyMuPDF V1)
  2. Reports parser version, signature, quality metrics
  3. Detects tables, Unicode issues, orphan numerics
  4. Classifies errors and failures
  5. Outputs machine-readable JSON and human-readable Markdown

V2-0 does NOT modify production ingestion, database, API, or index logic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPLACEMENT_CHAR = "\ufffd"

_LOAD_SCRIPT = r"""
import json, sys
from pathlib import Path
from app.loaders.pdf import PdfLoader

path = Path(sys.argv[1])
try:
    doc = PdfLoader().load(path)
    out = {
        "title": doc.title,
        "metadata": doc.metadata,
        "paragraphs": [
            {
                "type": p.type,
                "content": p.content,
                "page": p.page,
                "line_start": p.line_start,
                "line_end": p.line_end,
                "metadata": p.metadata,
            }
            for p in doc.paragraphs
        ],
    }
    print(json.dumps(out, ensure_ascii=False))
except Exception as e:
    print(json.dumps({"error": str(e), "error_type": type(e).__name__}, ensure_ascii=False))
"""

_TABLE_SCRIPT = r"""
import json, sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    import pymupdf
    doc = pymupdf.open(str(path))
    tables = []
    for page_num in range(doc.page_count):
        page = doc[page_num]
        found = page.find_tables()
        for t in found:
            rows = t.extract()
            tables.append({
                "page": page_num + 1,
                "row_count": len(rows),
                "col_count": len(rows[0]) if rows else 0,
                "rows": rows,
            })
    doc.close()
    print(json.dumps({"tables": tables}, ensure_ascii=False, default=str))
except Exception as e:
    print(json.dumps({"error": str(e), "error_type": type(e).__name__}, ensure_ascii=False))
"""


@dataclass
class BaselineReport:
    """Baseline diagnostic report for a single PDF."""

    pdf_name: str
    pdf_path: str
    parser_id: str
    parser_version: str
    parser_signature: str
    elapsed_ms: int
    page_count: int = 0
    character_count: int = 0
    paragraph_count: int = 0
    table_count: int = 0
    table_details: list[dict[str, Any]] = field(default_factory=list)
    replacement_character_count: int = 0
    broken_unicode_count: int = 0
    orphan_numeric_ratio: float = 0.0
    reading_order_confidence: float = 0.0
    repeated_header_footer_ratio: float = 0.0
    warnings: list[str] = field(default_factory=list)
    hard_failures: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    text_anchors_found: list[str] = field(default_factory=list)
    text_anchors_missing: list[str] = field(default_factory=list)
    header_binding_correct: bool | None = None
    row_binding_correct: bool | None = None
    page_numbers_available: bool = False
    bbox_available: bool = False
    error_classification: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get_pymupdf_version() -> str:
    """Get PyMuPDF version without importing in-process."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import pymupdf; print(pymupdf.__version__)"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _compute_parser_signature(parser_id: str, parser_version: str) -> str:
    """Compute a stable parser signature (SHA-256 of canonical JSON)."""
    canonical = json.dumps(
        {
            "parser_id": parser_id,
            "parser_version": parser_version,
            "ir_schema_version": 1,
            "normalizer_version": "unicode-v1",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _load_pdf_subprocess(pdf_path: Path) -> dict[str, Any]:
    """Load PDF in subprocess to avoid PyMuPDF segfault."""
    result = subprocess.run(
        [sys.executable, "-c", _LOAD_SCRIPT, str(pdf_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        return {"error": result.stderr or result.stdout, "error_type": "subprocess"}
    return json.loads(result.stdout)


def _detect_tables_subprocess(pdf_path: Path) -> dict[str, Any]:
    """Detect tables using PyMuPDF find_tables in subprocess."""
    result = subprocess.run(
        [sys.executable, "-c", _TABLE_SCRIPT, str(pdf_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        return {"error": result.stderr or result.stdout, "error_type": "subprocess"}
    # Strip non-JSON lines (PyMuPDF may print warnings to stdout)
    stdout = result.stdout.strip()
    json_start = stdout.find("{")
    if json_start < 0:
        return {"error": "no JSON in output", "error_type": "subprocess"}
    return json.loads(stdout[json_start:])


def _count_replacement_chars(text: str) -> int:
    """Count Unicode replacement characters (U+FFFD)."""
    return text.count(REPLACEMENT_CHAR)


def _count_broken_unicode(text: str) -> int:
    """Count potential broken Unicode sequences."""
    count = 0
    for char in text:
        code = ord(char)
        if code in (0xFFFD, 0xFFFE, 0xFFFF) or (0xD800 <= code <= 0xDFFF):
            count += 1
    return count


def _find_orphan_numerics(paragraphs: list[dict[str, Any]]) -> tuple[int, int]:
    """Detect numeric values not adjacent to text labels.

    Returns (orphan_count, total_numeric_count).
    """
    orphan_count = 0
    total_numeric = 0
    numeric_pattern = re.compile(r"\b\d+\.?\d*(?:±\d+\.?\d*)?\b")
    label_words = {
        "fid",
        "is",
        "acc",
        "accuracy",
        "score",
        "fidtr",
        "model",
        "method",
        "ours",
        "baseline",
        "epoch",
        "step",
        "loss",
        "params",
        "min",
        "max",
        "mean",
        "std",
        "±",
        "%",
        "bleu",
        "map",
        "precision",
        "recall",
        "f1",
        "auc",
        "mse",
        "psnr",
        "ssim",
    }

    for para in paragraphs:
        content = para.get("content", "")
        matches = list(numeric_pattern.finditer(content))
        total_numeric += len(matches)
        for match in matches:
            start = max(0, match.start() - 30)
            end = min(len(content), match.end() + 30)
            context_words = set(re.findall(r"\b\w+\b", content[start:end].lower()))
            if not context_words & label_words:
                orphan_count += 1
    return orphan_count, total_numeric


def _compute_reading_order_confidence(paragraphs: list[dict[str, Any]]) -> float:
    """Heuristic reading order confidence (0..1).

    V1 PdfLoader sorts by vertical band then x-coordinate.
    Confidence is reduced when:
    - Paragraphs on the same page are not in page order
    - Multiple paragraphs share the same page (potential interleaving)
    """
    if not paragraphs:
        return 0.0
    pages = [p.get("page", 0) for p in paragraphs if p.get("page")]
    if not pages:
        return 0.0
    sorted_pages = sorted(pages)
    in_order = pages == sorted_pages
    unique_pages = set(pages)
    multi_para_pages = len([p for p in unique_pages if pages.count(p) > 2])
    confidence = 1.0 if in_order else 0.7
    if multi_para_pages > 0:
        confidence -= 0.1 * multi_para_pages
    return max(0.0, min(1.0, confidence))


def _check_text_anchors(
    paragraphs: list[dict[str, Any]], expected_anchors: list[str]
) -> tuple[list[str], list[str]]:
    """Check which text anchors are found in the parsed text."""
    full_text = " ".join(p.get("content", "") for p in paragraphs)
    found = []
    missing = []
    for anchor in expected_anchors:
        if anchor.lower() in full_text.lower():
            found.append(anchor)
        else:
            missing.append(anchor)
    return found, missing


def _check_header_binding(
    paragraphs: list[dict[str, Any]],
    expected_binding: dict[str, list[str]],
) -> bool | None:
    """Check if header-to-value binding is verifiable.

    Returns True if all values appear in text, None if structure cannot be
    verified (V1 limitation), False if values are missing entirely.
    """
    full_text = " ".join(p.get("content", "") for p in paragraphs)
    all_values_present = True
    any_value_found = False
    for _header, values in expected_binding.items():
        for v in values:
            if v.lower() in full_text.lower():
                any_value_found = True
            else:
                all_values_present = False
    if not any_value_found:
        return False
    if all_values_present:
        return None
    return False


def _classify_error(report: BaselineReport) -> str | None:
    """Classify the error type if any."""
    if report.error_code is not None:
        return report.error_code
    if report.hard_failures:
        return "LAYOUT_INVALID"
    if report.replacement_character_count > 0:
        return "UNICODE_CORRUPT"
    if report.orphan_numeric_ratio > 0.05:
        return "ORPHAN_NUMERIC_HIGH"
    return None


def diagnose_pdf(
    pdf_path: Path,
    expected_anchors: list[str] | None = None,
    expected_binding: dict[str, list[str]] | None = None,
) -> BaselineReport:
    """Run baseline diagnostic on a single PDF.

    Args:
        pdf_path: Path to the PDF file.
        expected_anchors: Text patterns expected to appear in parsed text.
        expected_binding: Expected header-to-value bindings.

    Returns:
        BaselineReport with all diagnostic data.
    """
    parser_id = "pymupdf"
    parser_version = _get_pymupdf_version()
    parser_signature = _compute_parser_signature(parser_id, parser_version)

    start = time.perf_counter()
    report = BaselineReport(
        pdf_name=pdf_path.name,
        pdf_path=str(pdf_path),
        parser_id=parser_id,
        parser_version=parser_version,
        parser_signature=parser_signature,
        elapsed_ms=0,
    )

    # Load PDF
    load_result = _load_pdf_subprocess(pdf_path)
    elapsed = int((time.perf_counter() - start) * 1000)
    report.elapsed_ms = elapsed

    if "error" in load_result:
        report.error_code = "PDF_PARSE_FAILED"
        report.error_message = load_result["error"]
        report.error_classification = "PDF_PARSE_FAILED"
        return report

    paragraphs = load_result.get("paragraphs", [])
    metadata = load_result.get("metadata", {})

    report.page_count = metadata.get("page_count", 0)
    report.character_count = metadata.get("character_count", 0)
    report.paragraph_count = len(paragraphs)
    report.page_numbers_available = any(p.get("page") is not None for p in paragraphs)

    # Unicode analysis
    full_text = " ".join(p.get("content", "") for p in paragraphs)
    report.replacement_character_count = _count_replacement_chars(full_text)
    report.broken_unicode_count = _count_broken_unicode(full_text)

    # Table detection
    table_result = _detect_tables_subprocess(pdf_path)
    if "error" not in table_result:
        tables = table_result.get("tables", [])
        report.table_count = len(tables)
        report.table_details = tables
        report.bbox_available = any(t.get("row_count", 0) > 0 for t in tables)
    else:
        report.warnings.append("table_detection_failed")

    # Orphan numeric analysis
    orphan_count, total_numeric = _find_orphan_numerics(paragraphs)
    report.orphan_numeric_ratio = orphan_count / total_numeric if total_numeric > 0 else 0.0

    # Reading order confidence
    report.reading_order_confidence = _compute_reading_order_confidence(paragraphs)

    # Text anchor checking
    if expected_anchors:
        found, missing = _check_text_anchors(paragraphs, expected_anchors)
        report.text_anchors_found = found
        report.text_anchors_missing = missing

    # Header binding checking
    if expected_binding:
        report.header_binding_correct = _check_header_binding(paragraphs, expected_binding)
        # V1 cannot verify row binding
        report.row_binding_correct = None

    # Error classification
    report.error_classification = _classify_error(report)

    return report


def diagnose_fixtures(fixtures_dir: Path) -> list[BaselineReport]:
    """Run baseline diagnostic on all V2-0 synthetic fixtures.

    Each fixture has a corresponding golden JSON that defines expected anchors.
    """
    golden_dir = fixtures_dir / "golden"
    reports: list[BaselineReport] = []

    for golden_path in sorted(golden_dir.glob("*.json")):
        golden = json.loads(golden_path.read_text(encoding="utf-8"))
        fixture_name = golden["fixture"]
        pdf_path = fixtures_dir / f"{fixture_name}.pdf"

        if not pdf_path.exists():
            # Generate fixture if missing
            from tests.fixtures.pdf_v2.generators import _run

            _run(fixture_name, pdf_path)

        report = diagnose_pdf(
            pdf_path,
            expected_anchors=golden.get("expected_text_anchors", []),
            expected_binding=golden.get("expected_header_binding", {}),
        )
        report.bbox_available = report.bbox_available or golden.get("expected_table_count", 0) == 0
        reports.append(report)

    return reports


def format_report_markdown(reports: list[BaselineReport]) -> str:
    """Format baseline reports as human-readable Markdown."""
    lines = [
        "# PDF Ingestion V2-0 Baseline Report",
        "",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "Parser: PyMuPDF",
        f"Total PDFs: {len(reports)}",
        "",
        "## Summary",
        "",
        "| PDF | Pages | Chars | Tables | Repl. Chars | Orphan Num. Ratio | Reading Order | Parse OK |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    for r in reports:
        parse_ok = "✓" if r.error_code is None else "✗"
        lines.append(
            f"| {r.pdf_name} "
            f"| {r.page_count} "
            f"| {r.character_count} "
            f"| {r.table_count} "
            f"| {r.replacement_character_count} "
            f"| {r.orphan_numeric_ratio:.3f} "
            f"| {r.reading_order_confidence:.2f} "
            f"| {parse_ok} |"
        )

    lines.extend(["", "## Details", ""])

    for r in reports:
        lines.extend(
            [
                f"### {r.pdf_name}",
                "",
                f"- Parser: {r.parser_id} v{r.parser_version} (sig: {r.parser_signature})",
                f"- Elapsed: {r.elapsed_ms} ms",
                f"- Pages: {r.page_count}",
                f"- Characters: {r.character_count}",
                f"- Paragraphs: {r.paragraph_count}",
                f"- Tables detected: {r.table_count}",
                f"- Replacement chars: {r.replacement_character_count}",
                f"- Broken Unicode: {r.broken_unicode_count}",
                f"- Orphan numeric ratio: {r.orphan_numeric_ratio:.3f}",
                f"- Reading order confidence: {r.reading_order_confidence:.2f}",
                f"- Page numbers available: {r.page_numbers_available}",
                f"- BBox available: {r.bbox_available}",
                f"- Header binding correct: {r.header_binding_correct}",
                f"- Row binding correct: {r.row_binding_correct}",
                f"- Error code: {r.error_code or 'none'}",
                f"- Error classification: {r.error_classification or 'none'}",
            ]
        )
        if r.text_anchors_found:
            lines.append(f"- Anchors found: {', '.join(r.text_anchors_found)}")
        if r.text_anchors_missing:
            lines.append(f"- Anchors missing: {', '.join(r.text_anchors_missing)}")
        if r.warnings:
            lines.append(f"- Warnings: {', '.join(r.warnings)}")
        if r.hard_failures:
            lines.append(f"- Hard failures: {', '.join(r.hard_failures)}")
        lines.append("")

    return "\n".join(lines)


def save_reports(
    reports: list[BaselineReport],
    output_dir: Path,
    *,
    prefix: str = "baseline",
) -> tuple[Path, Path]:
    """Save reports as JSON and Markdown."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{prefix}.json"
    md_path = output_dir / f"{prefix}.md"

    json_data = {
        "parser_id": "pymupdf",
        "parser_version": reports[0].parser_version if reports else "unknown",
        "parser_signature": reports[0].parser_signature if reports else "",
        "total_pdfs": len(reports),
        "reports": [r.to_dict() for r in reports],
    }
    json_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(format_report_markdown(reports), encoding="utf-8")
    return json_path, md_path


def main() -> None:
    """CLI entry point for baseline diagnostic."""
    parser = argparse.ArgumentParser(description="PDF Ingestion V2-0 Baseline Diagnostic")
    parser.add_argument(
        "--input",
        type=Path,
        help="Path to a single PDF to diagnose",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        help="Path to V2-0 fixtures directory to diagnose all synthetic fixtures",
    )
    parser.add_argument(
        "--hard-cases",
        action="store_true",
        help="Run 11 hard cases baseline (requires private benchmark data)",
    )
    parser.add_argument(
        "--benchmark-dir",
        type=Path,
        default=Path("eval/private_benchmark"),
        help="Directory containing private benchmark PDFs and dataset",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("eval/runs/pdf_v2"),
        help="Output directory for reports",
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default="baseline",
        help="Prefix for output files",
    )
    args = parser.parse_args()

    if args.fixtures:
        reports = diagnose_fixtures(args.fixtures)
        json_path, md_path = save_reports(reports, args.output, prefix=args.prefix)
        print(f"Reports saved: {json_path}, {md_path}")
    elif args.input:
        report = diagnose_pdf(args.input)
        json_path, md_path = save_reports([report], args.output, prefix=args.prefix)
        print(f"Report saved: {json_path}, {md_path}")
    elif args.hard_cases:
        from eval.hard_cases import run_hard_cases_baseline

        reports = run_hard_cases_baseline(args.benchmark_dir)
        json_path, md_path = save_reports(reports, args.output, prefix="hard_cases")
        print(f"Reports saved: {json_path}, {md_path}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

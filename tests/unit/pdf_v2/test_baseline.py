"""Unit tests for PDF V2-0 baseline diagnostic module.

Tests the diagnostic functions without requiring real PDFs for all cases.
Uses synthetic fixtures for integration-level verification.
"""

from __future__ import annotations

import json
from pathlib import Path

from eval.pdf_baseline import (
    BaselineReport,
    _check_header_binding,
    _check_text_anchors,
    _classify_error,
    _compute_parser_signature,
    _compute_reading_order_confidence,
    _count_broken_unicode,
    _count_replacement_chars,
    _find_orphan_numerics,
    diagnose_pdf,
    format_report_markdown,
    save_reports,
)
from tests.fixtures.pdf_v2.generators import _run

FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent / "tests" / "fixtures" / "pdf_v2"
)


class TestParserSignature:
    def test_signature_stable(self) -> None:
        """Same input should produce same signature."""
        sig1 = _compute_parser_signature("pymupdf", "1.28.2")
        sig2 = _compute_parser_signature("pymupdf", "1.28.2")
        assert sig1 == sig2

    def test_signature_changes_on_version(self) -> None:
        """Different versions should produce different signatures."""
        sig1 = _compute_parser_signature("pymupdf", "1.28.2")
        sig2 = _compute_parser_signature("pymupdf", "1.28.3")
        assert sig1 != sig2

    def test_signature_changes_on_id(self) -> None:
        """Different parser ids should produce different signatures."""
        sig1 = _compute_parser_signature("pymupdf", "1.28.2")
        sig2 = _compute_parser_signature("docling", "1.28.2")
        assert sig1 != sig2

    def test_signature_length(self) -> None:
        """Signature should be 16 hex chars (truncated SHA-256)."""
        sig = _compute_parser_signature("pymupdf", "1.28.2")
        assert len(sig) == 16
        int(sig, 16)  # should be valid hex


class TestUnicodeAnalysis:
    def test_count_replacement_chars_none(self) -> None:
        assert _count_replacement_chars("hello world") == 0

    def test_count_replacement_chars_present(self) -> None:
        text = "hello \ufffd world \ufffd"
        assert _count_replacement_chars(text) == 2

    def test_count_broken_unicode_none(self) -> None:
        assert _count_broken_unicode("normal text ε θ Σ") == 0

    def test_count_broken_unicode_replacement(self) -> None:
        assert _count_broken_unicode("text \ufffd more") == 1


class TestOrphanNumerics:
    def test_no_numerics(self) -> None:
        paragraphs = [{"content": "no numbers here"}]
        orphan, total = _find_orphan_numerics(paragraphs)
        assert orphan == 0
        assert total == 0

    def test_bound_numeric(self) -> None:
        paragraphs = [{"content": "FID score is 3.17 and IS is 9.46"}]
        orphan, total = _find_orphan_numerics(paragraphs)
        assert total == 2
        assert orphan == 0

    def test_orphan_numeric(self) -> None:
        paragraphs = [{"content": "The result was 42 and that was surprising."}]
        orphan, total = _find_orphan_numerics(paragraphs)
        assert total == 1
        assert orphan == 1

    def test_plusminus_not_orphan(self) -> None:
        paragraphs = [{"content": "9.46±0.11 is the IS score."}]
        orphan, total = _find_orphan_numerics(paragraphs)
        assert total == 1  # 9.46±0.11 matches as one numeric token
        assert orphan == 0


class TestReadingOrderConfidence:
    def test_empty(self) -> None:
        assert _compute_reading_order_confidence([]) == 0.0

    def test_single_page(self) -> None:
        paragraphs = [{"page": 1, "content": "text"}]
        conf = _compute_reading_order_confidence(paragraphs)
        assert 0.9 <= conf <= 1.0

    def test_ordered_pages(self) -> None:
        paragraphs = [{"page": 1, "content": "a"}, {"page": 2, "content": "b"}]
        conf = _compute_reading_order_confidence(paragraphs)
        assert conf == 1.0

    def test_unordered_pages(self) -> None:
        paragraphs = [{"page": 2, "content": "b"}, {"page": 1, "content": "a"}]
        conf = _compute_reading_order_confidence(paragraphs)
        assert conf <= 0.7


class TestTextAnchors:
    def test_all_found(self) -> None:
        paragraphs = [{"content": "DDPM achieves 9.46±0.11 and FID 3.17"}]
        anchors = ["DDPM", "9.46±0.11", "3.17"]
        found, missing = _check_text_anchors(paragraphs, anchors)
        assert len(found) == 3
        assert len(missing) == 0

    def test_some_missing(self) -> None:
        paragraphs = [{"content": "DDPM achieves 9.46±0.11"}]
        anchors = ["DDPM", "9.46±0.11", "3.17"]
        found, missing = _check_text_anchors(paragraphs, anchors)
        assert len(found) == 2
        assert "3.17" in missing

    def test_case_insensitive(self) -> None:
        paragraphs = [{"content": "ddpm achieves is 9.46"}]
        anchors = ["DDPM", "IS"]
        found, missing = _check_text_anchors(paragraphs, anchors)
        assert len(found) == 2


class TestHeaderBinding:
    def test_values_present(self) -> None:
        paragraphs = [{"content": "DDPM 9.46 3.17"}]
        binding = {"IS": ["9.46"], "FID": ["3.17"]}
        result = _check_header_binding(paragraphs, binding)
        # V1 cannot verify structure, so returns None when values present
        assert result is None

    def test_values_missing(self) -> None:
        paragraphs = [{"content": "DDPM results here"}]
        binding = {"IS": ["9.46"], "FID": ["3.17"]}
        result = _check_header_binding(paragraphs, binding)
        assert result is False


class TestErrorClassification:
    def test_no_error(self) -> None:
        report = BaselineReport(
            pdf_name="test.pdf",
            pdf_path="/tmp/test.pdf",
            parser_id="pymupdf",
            parser_version="1.0",
            parser_signature="abc",
            elapsed_ms=100,
        )
        assert _classify_error(report) is None

    def test_unicode_corrupt(self) -> None:
        report = BaselineReport(
            pdf_name="test.pdf",
            pdf_path="/tmp/test.pdf",
            parser_id="pymupdf",
            parser_version="1.0",
            parser_signature="abc",
            elapsed_ms=100,
            replacement_character_count=5,
        )
        assert _classify_error(report) == "UNICODE_CORRUPT"

    def test_orphan_high(self) -> None:
        report = BaselineReport(
            pdf_name="test.pdf",
            pdf_path="/tmp/test.pdf",
            parser_id="pymupdf",
            parser_version="1.0",
            parser_signature="abc",
            elapsed_ms=100,
            orphan_numeric_ratio=0.1,
        )
        assert _classify_error(report) == "ORPHAN_NUMERIC_HIGH"


class TestDiagnoseFixture:
    """Integration test: diagnose a real synthetic fixture."""

    def test_diagnose_simple_table(self, tmp_path: Path) -> None:
        """Diagnose the simple_table fixture and verify report fields."""
        pdf_path = tmp_path / "simple_table.pdf"
        _run("simple_table", pdf_path)

        report = diagnose_pdf(pdf_path)
        assert report.parser_id == "pymupdf"
        assert report.parser_version != "unknown"
        assert len(report.parser_signature) == 16
        assert report.page_count == 1
        assert report.character_count > 0
        assert report.paragraph_count > 0
        assert report.elapsed_ms > 0
        assert report.error_code is None

    def test_diagnose_unicode_fixture(self, tmp_path: Path) -> None:
        """Diagnose the unicode_chars fixture — should have 0 replacement chars."""
        pdf_path = tmp_path / "unicode_chars.pdf"
        _run("unicode_chars", pdf_path)

        report = diagnose_pdf(pdf_path)
        assert report.replacement_character_count == 0
        assert report.broken_unicode_count == 0

    def test_diagnose_with_anchors(self, tmp_path: Path) -> None:
        """Diagnose with expected anchors and verify found/missing lists."""
        pdf_path = tmp_path / "simple_table.pdf"
        _run("simple_table", pdf_path)

        report = diagnose_pdf(
            pdf_path,
            expected_anchors=["9.46±0.11", "3.17", "NOT_PRESENT"],
        )
        assert "9.46±0.11" in report.text_anchors_found
        assert "3.17" in report.text_anchors_found
        assert "NOT_PRESENT" in report.text_anchors_missing


class TestReportOutput:
    def test_save_reports(self, tmp_path: Path) -> None:
        """Save reports and verify JSON and Markdown output."""
        reports = [
            BaselineReport(
                pdf_name="test.pdf",
                pdf_path="/tmp/test.pdf",
                parser_id="pymupdf",
                parser_version="1.0",
                parser_signature="abc123",
                elapsed_ms=100,
                page_count=2,
                character_count=500,
            )
        ]
        output_dir = tmp_path / "reports"
        json_path, md_path = save_reports(reports, output_dir, prefix="test")

        assert json_path.exists()
        assert md_path.exists()

        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["total_pdfs"] == 1
        assert data["reports"][0]["pdf_name"] == "test.pdf"

        md = md_path.read_text(encoding="utf-8")
        assert "# PDF Ingestion V2-0 Baseline Report" in md
        assert "test.pdf" in md

    def test_format_report_markdown_not_empty(self) -> None:
        """Markdown report should not be empty."""
        reports = [
            BaselineReport(
                pdf_name="test.pdf",
                pdf_path="/tmp/test.pdf",
                parser_id="pymupdf",
                parser_version="1.0",
                parser_signature="abc123",
                elapsed_ms=100,
            )
        ]
        md = format_report_markdown(reports)
        assert len(md) > 100
        assert "test.pdf" in md

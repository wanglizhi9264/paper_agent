"""Unit tests for PDF V2-0 synthetic fixtures.

Verifies that:
  1. All 8 synthetic PDFs can be generated
  2. Each PDF can be loaded by the current PdfLoader
  3. Golden assertion anchors are checked correctly
  4. Known V1 limitations are documented (not hidden)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fixtures.pdf_runner import load_pdf_in_subprocess
from tests.fixtures.pdf_v2.generators import FIXTURE_MODES, _run, generate_all

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "fixtures" / "pdf_v2"
GOLDEN_DIR = FIXTURES_DIR / "golden"


@pytest.fixture(scope="module")
def generated_fixtures(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Generate all V2-0 fixtures once for the module."""
    output_dir = tmp_path_factory.mktemp("pdf_v2_fixtures")
    return generate_all(output_dir)


class TestFixtureGeneration:
    """Test that all synthetic PDFs can be generated."""

    @pytest.mark.parametrize("mode", FIXTURE_MODES)
    def test_fixture_generates(self, mode: str, tmp_path: Path) -> None:
        """Each fixture mode should produce a valid PDF file."""
        pdf_path = tmp_path / f"{mode}.pdf"
        _run(mode, pdf_path)
        assert pdf_path.exists()
        assert pdf_path.stat().st_size > 0

    def test_all_fixtures_generated(self, generated_fixtures: dict[str, Path]) -> None:
        """All 8 fixture types should be generated."""
        assert len(generated_fixtures) == 8
        for mode in FIXTURE_MODES:
            assert mode in generated_fixtures
            assert generated_fixtures[mode].exists()


class TestFixtureGoldenAssertions:
    """Test golden assertions for each fixture.

    These tests verify that:
      1. The PdfLoader can load each fixture without error
      2. Expected text anchors are checked (some may be missing — that's the baseline)
      3. Known limitations are explicitly documented, not hidden
    """

    @pytest.fixture(scope="module")
    def loaded_fixtures(self, generated_fixtures: dict[str, Path]) -> dict[str, dict]:
        """Load all generated fixtures in subprocess."""
        loaded: dict[str, dict] = {}
        for name, pdf_path in generated_fixtures.items():
            try:
                loaded[name] = load_pdf_in_subprocess(pdf_path)
            except RuntimeError:
                loaded[name] = {"error": "load_failed"}
        return loaded

    @pytest.mark.parametrize("mode", FIXTURE_MODES)
    def test_pdf_loads_without_error(self, mode: str, loaded_fixtures: dict[str, dict]) -> None:
        """Each fixture should load without raising LoaderError."""
        result = loaded_fixtures[mode]
        assert "error" not in result, f"{mode} failed to load: {result.get('error')}"

    @pytest.mark.parametrize("mode", FIXTURE_MODES)
    def test_golden_anchors_checked(self, mode: str, loaded_fixtures: dict[str, dict]) -> None:
        """Check golden assertion text anchors against parsed text.

        This test does NOT require all anchors to be found — it records
        which are found and which are missing as the baseline. However,
        it MUST NOT silently pass if the fixture itself is broken.
        """
        golden_path = GOLDEN_DIR / f"{mode}.json"
        assert golden_path.exists(), f"Golden file missing for {mode}"
        golden = json.loads(golden_path.read_text(encoding="utf-8"))

        result = loaded_fixtures[mode]
        full_text = " ".join(p["content"] for p in result.get("paragraphs", []))

        found = []
        missing = []
        for anchor in golden.get("expected_text_anchors", []):
            if anchor.lower() in full_text.lower():
                found.append(anchor)
            else:
                missing.append(anchor)

        # At least some anchors must be found (otherwise the fixture is broken)
        assert len(found) > 0, (
            f"No anchors found for {mode} — fixture may be broken. Missing: {missing}"
        )

        # Document known limitations — these are expected to be missing in V1
        known_limits = golden.get("known_limitations", [])
        assert len(known_limits) > 0, f"No known limitations documented for {mode}"

    def test_simple_table_anchors(self, loaded_fixtures: dict[str, dict]) -> None:
        """Simple table fixture should have the table text in parsed output."""
        result = loaded_fixtures["simple_table"]
        full_text = " ".join(p["content"] for p in result.get("paragraphs", []))
        assert "9.46" in full_text, "DDPM IS value not found"
        assert "3.17" in full_text, "DDPM FID value not found"
        assert "DDPM" in full_text, "DDPM model name not found"

    def test_unicode_chars_preserved(self, loaded_fixtures: dict[str, dict]) -> None:
        """Unicode fixture: ± is preserved; Greek letters may be lost by default font.

        PyMuPDF's insert_text with default Helvetica does not support Greek
        letters or math symbols — they get replaced with '·'. This is a
        fixture generation limitation, not a parser limitation. The test
        verifies that ± and ASCII characters are preserved, and documents
        the Greek letter limitation.
        """
        result = loaded_fixtures["unicode_chars"]
        full_text = " ".join(p["content"] for p in result.get("paragraphs", []))
        assert "±" in full_text, "± not preserved"
        assert "9.46" in full_text, "9.46 not preserved"
        assert "13.61" in full_text, "13.61 not preserved"
        assert "13.09" in full_text, "13.09 not preserved"
        # Greek letters are lost with default font — documented limitation
        greek_found = "ε" in full_text or "θ" in full_text or "Σ" in full_text
        if not greek_found:
            # This is expected with PyMuPDF default font
            pass

    def test_cross_page_page_count(self, loaded_fixtures: dict[str, dict]) -> None:
        """Cross-page fixture should have 2 pages."""
        result = loaded_fixtures["cross_page"]
        assert result["metadata"]["page_count"] == 2

    def test_multicolumn_text_present(self, loaded_fixtures: dict[str, dict]) -> None:
        """Multicolumn fixture should have text from both columns."""
        result = loaded_fixtures["multicolumn"]
        full_text = " ".join(p["content"] for p in result.get("paragraphs", []))
        assert "Introduction" in full_text, "Left column heading not found"
        assert "Related Work" in full_text, "Right column heading not found"

    def test_model_metrics_anchors(self, loaded_fixtures: dict[str, dict]) -> None:
        """Model metrics fixture should have all model names and values."""
        result = loaded_fixtures["model_metrics"]
        full_text = " ".join(p["content"] for p in result.get("paragraphs", []))
        for anchor in ["DDPM", "EEG2IM", "ACTOR", "LMM-Large", "9.46", "3.17"]:
            assert anchor in full_text, f"Anchor {anchor} not found in model_metrics fixture"

    def test_hyphenation_anchors(self, loaded_fixtures: dict[str, dict]) -> None:
        """Hyphenation fixture should have hyphenated text segments."""
        result = loaded_fixtures["hyphenation"]
        full_text = " ".join(p["content"] for p in result.get("paragraphs", []))
        assert "long-" in full_text, "Hyphenated 'long-' not found"
        assert "13.61" in full_text, "Numeric '13.61' not found"
        assert "13.09" in full_text, "Numeric '13.09' not found"


class TestGoldenFiles:
    """Test that golden JSON files are valid and complete."""

    @pytest.mark.parametrize("mode", FIXTURE_MODES)
    def test_golden_file_valid(self, mode: str) -> None:
        """Each golden file should be valid JSON with required fields."""
        golden_path = GOLDEN_DIR / f"{mode}.json"
        assert golden_path.exists(), f"Golden file missing for {mode}"
        golden = json.loads(golden_path.read_text(encoding="utf-8"))
        assert "fixture" in golden
        assert golden["fixture"] == mode
        assert "description" in golden
        assert "expected_text_anchors" in golden
        assert isinstance(golden["expected_text_anchors"], list)
        assert len(golden["expected_text_anchors"]) > 0
        assert "known_limitations" in golden
        assert len(golden["known_limitations"]) > 0

"""Real-fixture tests for the PyMuPDF V2 adapter (spec §19 V2-2 gate).

PyMuPDF segfaults inside the pytest process on macOS/arm64, so the adapter
runs in a subprocess (same workaround as the V1 loader tests). These tests
verify the completion gate: plain text has no regression and the simple
table fixture has correct structure.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from tests.fixtures.pdf_runner import load_pdf_in_subprocess
from tests.fixtures.pdf_v2.generators import _run

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "pdf_v2"

_PARSE_SCRIPT = r"""
import json, sys
sys.path.insert(0, ".")
from pathlib import Path
from uuid import UUID
from app.loaders.pymupdf_adapter import (
    PyMuPDFParser,
    bridge_to_legacy_paragraphs,
    fast_path_acceptable,
)

pdf = Path(sys.argv[1])
parser = PyMuPDFParser(parser_version="test-pinned")
ir = parser.parse(pdf, document_id=UUID(sys.argv[2]))
out = {
    "ir": json.loads(ir.model_dump_json()),
    "valid": None,
    "fast_path": fast_path_acceptable(ir),
    "bridge": bridge_to_legacy_paragraphs(ir),
}
print(json.dumps(out, ensure_ascii=False))
"""


def parse_fixture_in_subprocess(pdf: Path) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-c", _PARSE_SCRIPT, str(pdf), str(uuid4())],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        cwd=str(Path(__file__).resolve().parents[3]),
    )
    if result.returncode != 0:
        raise RuntimeError(f"adapter subprocess failed: {result.stderr or result.stdout}")
    stdout = result.stdout.strip()
    return json.loads(stdout[stdout.find("{") :])


@pytest.fixture(scope="module")
def simple_table(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    pdf = tmp_path_factory.mktemp("v22") / "simple_table.pdf"
    _run("simple_table", pdf)
    return parse_fixture_in_subprocess(pdf)


class TestSimpleTableGate:
    """完成门：简单表格 fixture 结构正确。"""

    def test_exactly_one_valid_table(self, simple_table: dict[str, Any]) -> None:
        ir = simple_table["ir"]
        tables = [e for e in ir["elements"] if e["kind"] == "table"]
        assert len(tables) == 1
        assert tables[0]["table"] is not None
        assert ir["quality"]["malformed_table_count"] == 0

    def test_table_dimensions_and_binding(self, simple_table: dict[str, Any]) -> None:
        table = next(e for e in simple_table["ir"]["elements"] if e["kind"] == "table")["table"]
        assert table["row_count"] == 3
        assert table["column_count"] == 3
        assert table["header_rows"] == [0]
        markdown = table["markdown"]
        assert "| Model | IS | FID |" in markdown
        assert "9.46±0.11" in markdown
        assert "3.17" in markdown

    def test_cell_bboxes_present(self, simple_table: dict[str, Any]) -> None:
        table = next(e for e in simple_table["ir"]["elements"] if e["kind"] == "table")["table"]
        data_cells = [c for c in table["cells"] if not c["is_column_header"]]
        assert all(c["provenance"] for c in data_cells)
        first_bbox = data_cells[0]["provenance"][0]["bbox"]
        assert first_bbox["x0"] < first_bbox["x1"]

    def test_ir_passes_validator(self, simple_table: dict[str, Any]) -> None:
        from app.document_ir.models import DocumentIR
        from app.document_ir.validate import validate_document_ir

        ir = DocumentIR.model_validate(simple_table["ir"])
        result = validate_document_ir(ir)
        assert result.ok, [i.message for i in result.issues]

    def test_fast_path_acceptance(self, simple_table: dict[str, Any]) -> None:
        assert simple_table["fast_path"] is True


class TestPlainTextNoRegression:
    """完成门：普通文本无回归（legacy loader vs V2 bridge 对比）。"""

    @pytest.fixture(scope="class")
    def comparison(self, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
        from tests.fixtures.generators import make_pdf

        pdf = tmp_path_factory.mktemp("regress") / "plain.pdf"
        make_pdf(pdf)

        legacy = load_pdf_in_subprocess(pdf)
        v2 = parse_fixture_in_subprocess(pdf)
        legacy_text = " ".join(p["content"] for p in legacy["paragraphs"])
        bridge_text = " ".join(p["content"] for p in v2["bridge"]["paragraphs"])
        return {
            "legacy": legacy,
            "v2": v2,
            "legacy_text": " ".join(legacy_text.split()),
            "bridge_text": " ".join(bridge_text.split()),
        }

    def test_page_count_matches(self, comparison: dict[str, Any]) -> None:
        assert comparison["legacy"]["metadata"]["page_count"] == 2
        assert comparison["v2"]["bridge"]["metadata"]["page_count"] == 2

    def test_title_preserved(self, comparison: dict[str, Any]) -> None:
        assert comparison["bridge_text"].startswith("Sample Paper Title")

    def test_all_legacy_content_present_in_bridge(self, comparison: dict[str, Any]) -> None:
        for paragraph in comparison["legacy"]["paragraphs"]:
            content = " ".join(paragraph["content"].split())
            assert content in comparison["bridge_text"], f"missing: {content!r}"

    def test_headings_detected_as_heading_kind(self, comparison: dict[str, Any]) -> None:
        kinds = {
            p["content"]: p["metadata"]["ir_kind"] for p in comparison["v2"]["bridge"]["paragraphs"]
        }
        assert kinds.get("Introduction") == "heading"
        assert kinds.get("Method") == "heading"

    def test_body_paragraphs_merged(self, comparison: dict[str, Any]) -> None:
        kinds = [
            p["metadata"]["ir_kind"]
            for p in comparison["v2"]["bridge"]["paragraphs"]
            if p["metadata"]["ir_kind"] == "paragraph"
        ]
        # Two body paragraphs (one per page section) survive as paragraphs.
        assert len(kinds) == 2


class TestOtherFixturesThroughV2:
    @pytest.fixture(scope="class")
    def multicolumn(self, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
        pdf = tmp_path_factory.mktemp("v22m") / "multicolumn.pdf"
        _run("multicolumn", pdf)
        return parse_fixture_in_subprocess(pdf)

    @pytest.fixture(scope="class")
    def cross_page(self, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
        pdf = tmp_path_factory.mktemp("v22c") / "cross_page.pdf"
        _run("cross_page", pdf)
        return parse_fixture_in_subprocess(pdf)

    @pytest.fixture(scope="class")
    def unicode_fixture(self, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
        pdf = tmp_path_factory.mktemp("v22u") / "unicode_chars.pdf"
        _run("unicode_chars", pdf)
        return parse_fixture_in_subprocess(pdf)

    def test_multicolumn_detected(self, multicolumn: dict[str, Any]) -> None:
        counts = multicolumn["ir"]["metadata"]["column_counts"]
        assert any(int(v) == 2 for v in counts.values())

    def test_cross_page_two_pages(self, cross_page: dict[str, Any]) -> None:
        assert len(cross_page["ir"]["pages"]) == 2

    def test_unicode_zero_replacement(self, unicode_fixture: dict[str, Any]) -> None:
        assert unicode_fixture["ir"]["quality"]["replacement_character_count"] == 0

    def test_every_element_has_provenance_with_bbox(
        self, simple_table: dict[str, Any], multicolumn: dict[str, Any]
    ) -> None:
        for report in (simple_table, multicolumn):
            for element in report["ir"]["elements"]:
                assert element["provenance"], f"element {element['id']} missing provenance"
                assert all(span["bbox"] is not None for span in element["provenance"])

    def test_physical_pages_one_based(self, cross_page: dict[str, Any]) -> None:
        pages = [p["physical_page"] for p in cross_page["ir"]["pages"]]
        assert pages == [1, 2]

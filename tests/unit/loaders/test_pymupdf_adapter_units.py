"""Tests for PyMuPDF V2 adapter pure assembly functions (spec §7.1, §8.3).

These tests exercise the JSON-primitive → DocumentIR builder without pymupdf
and without real PDFs, so they run fast and deterministically in-process.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.document_ir.errors import ParseError
from app.document_ir.validate import validate_document_ir
from app.loaders.pymupdf_adapter import (
    assign_column,
    build_document_ir,
    build_table_data_from_primitive,
    classify_line_kind,
    count_orphan_numeric_cells,
    detect_columns,
    find_repeated_band_texts,
    header_row_count,
    merge_lines_into_paragraphs,
)
from tests.unit.document_ir.builders import make_cell


def line(
    text: str,
    *,
    y: float,
    x: float = 72.0,
    size: float = 10.0,
    height: float = 12.0,
    x1: float | None = None,
) -> dict[str, Any]:
    return {
        "bbox": [x, y, x1 if x1 is not None else x + 200.0, y + height],
        "text": text,
        "spans": [
            {"text": text, "size": size, "font": "Helvetica", "bbox": [x, y, x + 200, y + height]}
        ],
    }


def page(
    lines: list[dict[str, Any]],
    *,
    number: int = 1,
    width: float = 612.0,
    height: float = 792.0,
    tables: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "physical_page": number,
        "width": width,
        "height": height,
        "blocks": [
            {
                "bbox": [
                    lines[0]["bbox"][0],
                    lines[0]["bbox"][1],
                    max(ln["bbox"][2] for ln in lines),
                    max(ln["bbox"][3] for ln in lines),
                ],
                "lines": lines,
            }
        ]
        if lines
        else [],
        "tables": tables or [],
    }


def payload(pages: list[dict[str, Any]]) -> dict[str, Any]:
    return {"page_count": len(pages), "pages": pages}


class TestColumnDetection:
    def test_single_column_when_blocks_span_middle(self) -> None:
        blocks = [{"bbox": [72, y, 500, y + 20]} for y in range(100, 400, 30)]
        # All wide blocks cross the midline: still one column, full crossing.
        assert detect_columns(blocks, 612.0) == (1, 1.0)

    def test_single_column_low_crossing(self) -> None:
        blocks = [{"bbox": [72, y, 300, y + 20]} for y in range(100, 400, 30)]
        count, ratio = detect_columns(blocks, 612.0)
        assert count == 1
        assert ratio == 0.0

    def test_two_columns_detected(self) -> None:
        left = [{"bbox": [60, y, 280, y + 20]} for y in range(80, 700, 40)]
        right = [{"bbox": [320, y, 550, y + 20]} for y in range(80, 700, 40)]
        count, ratio = detect_columns(left + right, 612.0)
        assert count == 2
        assert ratio == 0.0

    def test_too_few_blocks_is_single_column(self) -> None:
        blocks = [{"bbox": [60, 80, 280, 100]}, {"bbox": [330, 80, 550, 100]}]
        assert detect_columns(blocks, 612.0)[0] == 1

    def test_crossing_ratio_reported(self) -> None:
        blocks = [
            {"bbox": [60, 80, 280, 100]},
            {"bbox": [60, 120, 280, 140]},
            {"bbox": [330, 80, 550, 100]},
            {"bbox": [300, 200, 340, 220]},  # crosses the midline
        ]
        _, ratio = detect_columns(blocks, 612.0)
        assert abs(ratio - 0.25) < 1e-9

    def test_assign_column_left_right(self) -> None:
        assert assign_column([60, 0, 280, 20], 612.0, 2) == 0
        assert assign_column([330, 0, 550, 20], 612.0, 2) == 1


class TestHeaderFooterRepeats:
    def test_repeated_top_band_detected(self) -> None:
        pages = []
        for number in (1, 2, 3):
            header = {
                "bbox": [72, 20, 300, 40],
                "lines": [line("Neural Networks Journal", y=20, x=72)],
            }
            body = {
                "bbox": [72, 100, 500, 120],
                "lines": [line(f"Body text page {number}", y=100)],
            }
            pages.append(
                {
                    "physical_page": number,
                    "width": 612.0,
                    "height": 792.0,
                    "blocks": [header, body],
                    "tables": [],
                }
            )
        mapping, ratio = find_repeated_band_texts(pages, _norm_allow_empty)
        assert len(mapping) == 3
        assert all(v == "header" for v in mapping.values())
        assert ratio > 0

    def test_body_never_flagged(self) -> None:
        pages = [
            {
                "physical_page": n,
                "width": 612.0,
                "height": 792.0,
                "blocks": [
                    {"bbox": [72, 300, 500, 320], "lines": [line("same middle text", y=300)]}
                ],
                "tables": [],
            }
            for n in (1, 2, 3)
        ]
        mapping, ratio = find_repeated_band_texts(pages, _norm_allow_empty)
        assert mapping == {}
        assert ratio == 0.0

    def test_single_page_skipped(self) -> None:
        pages = [
            {
                "physical_page": 1,
                "width": 612.0,
                "height": 792.0,
                "blocks": [{"bbox": [72, 20, 300, 40], "lines": [line("header", y=20)]}],
                "tables": [],
            }
        ]
        assert find_repeated_band_texts(pages, _norm_allow_empty)[0] == {}


def _norm_allow_empty(text: str, *, allow_empty: bool = False) -> Any:
    from app.document_ir.normalize import normalize_for_retrieval

    return normalize_for_retrieval(text, allow_empty=allow_empty)


class TestParagraphMerging:
    def test_consecutive_lines_merge(self) -> None:
        lines = [line("first chunk", y=100), line("second chunk", y=114)]
        groups = merge_lines_into_paragraphs(lines, body_size=10.0, column_ids=[0, 0])
        assert len(groups) == 1

    def test_large_gap_starts_new_paragraph(self) -> None:
        lines = [line("para one", y=100), line("para two", y=160)]
        groups = merge_lines_into_paragraphs(lines, body_size=10.0, column_ids=[0, 0])
        assert len(groups) == 2

    def test_size_jump_prevents_merge(self) -> None:
        lines = [line("body", y=100, size=10), line("Heading", y=114, size=16)]
        groups = merge_lines_into_paragraphs(lines, body_size=10.0, column_ids=[0, 0])
        assert len(groups) == 2

    def test_column_change_prevents_merge(self) -> None:
        lines = [line("left end", y=100, x=60), line("right start", y=114, x=330)]
        groups = merge_lines_into_paragraphs(lines, body_size=10.0, column_ids=[0, 1])
        assert len(groups) == 2

    def test_list_item_never_merges_upward(self) -> None:
        lines = [line("intro sentence here", y=100), line("- bullet item", y=114)]
        groups = merge_lines_into_paragraphs(lines, body_size=10.0, column_ids=[0, 0])
        assert len(groups) == 2


class TestLineClassification:
    def test_heading_by_size(self) -> None:
        assert classify_line_kind("2 Method", 14.0, 10.0) == "heading"

    def test_caption_pattern(self) -> None:
        assert classify_line_kind("Table 1: results", 9.5, 10.0) == "caption"
        assert classify_line_kind("Figure 3 shows", 9.5, 10.0) == "caption"

    def test_list_marker(self) -> None:
        assert classify_line_kind("- item", 10.0, 10.0) == "list"
        assert classify_line_kind("1. item", 10.0, 10.0) == "list"

    def test_formula_symbols(self) -> None:
        assert classify_line_kind("L = E[||ε − εθ||²]", 10.0, 10.0) == "formula"

    def test_default_paragraph(self) -> None:
        assert classify_line_kind("Ordinary running text.", 10.0, 10.0) == "paragraph"


class TestHeaderRowCount:
    def test_rows_before_first_numeric(self) -> None:
        rows = [["Model", "FID"], ["DDPM", "3.17"]]
        assert header_row_count(rows) == 1

    def test_no_numeric_defaults_first_row(self) -> None:
        rows = [["A", "B"], ["C", "D"]]
        assert header_row_count(rows) == 1

    def test_multi_row_header(self) -> None:
        rows = [["G"], ["FID"], ["1.0"]]
        assert header_row_count(rows) == 2


class TestTableBuilding:
    def simple_table(self, *, geometry_verified: bool = True) -> dict[str, Any]:
        rows_text = [["Model", "IS", "FID"], ["DDPM", "9.46±0.11", "3.17"]]
        cell_bboxes = [
            [[72 + c * 100, 100, 172 + c * 100, 120] for c in range(3)] for _ in rows_text
        ]
        return {
            "bbox": [72.0, 100.0, 372.0, 140.0],
            "row_count": 2,
            "col_count": 3,
            "rows_text": rows_text,
            "cell_bboxes": cell_bboxes,
            "geometry_verified": geometry_verified,
        }

    def test_builds_valid_table(self) -> None:
        table, warnings = build_table_data_from_primitive(self.simple_table(), page=1)
        assert warnings == []
        assert table is not None
        assert table.row_count == 2
        assert table.header_rows == [0]
        ddpm_row = [c for c in table.cells if c.row == 1]
        assert [c.normalized_text for c in sorted(ddpm_row, key=lambda c: c.column)] == [
            "DDPM",
            "9.46±0.11",
            "3.17",
        ]

    def test_unverified_geometry_warns(self) -> None:
        primitive = self.simple_table(geometry_verified=False)
        table, warnings = build_table_data_from_primitive(primitive, page=1)
        assert table is not None
        assert any("PDF_TABLE_INVALID" in w for w in warnings)

    def test_ragged_extraction_rejected(self) -> None:
        primitive = self.simple_table()
        primitive["rows_text"] = [["Model", "IS"], ["DDPM", "9.46", "extra"]]  # type: ignore[assignment]
        primitive["col_count"] = 3
        table, warnings = build_table_data_from_primitive(primitive, page=1)
        assert table is None
        assert any("ragged" in w for w in warnings)


class TestOrphanNumerics:
    def test_bound_cells_not_orphan(self) -> None:
        cells = [
            make_cell(0, 0, "Model", is_column_header=True),
            make_cell(0, 1, "IS", is_column_header=True),
            make_cell(1, 0, "DDPM"),
            make_cell(1, 1, "9.46"),
        ]
        from app.document_ir.markdown import make_table_data as mtd

        table = mtd(cells, row_count=2, column_count=2, header_rows=[0])
        orphans, total = count_orphan_numeric_cells([table])
        assert total == 1
        assert orphans == 0

    def test_missing_row_label_is_orphan(self) -> None:
        cells = [
            make_cell(0, 0, "", is_column_header=True),
            make_cell(0, 1, "IS", is_column_header=True),
            make_cell(1, 0, ""),
            make_cell(1, 1, "9.46"),
        ]
        from app.document_ir.markdown import make_table_data as mtd

        table = mtd(cells, row_count=2, column_count=2, header_rows=[0])
        orphans, total = count_orphan_numeric_cells([table])
        assert total == 1
        assert orphans == 1


class TestBuildDocumentIR:
    def minimal(self, pages: list[dict[str, Any]], title: str = "T") -> Any:
        return build_document_ir(
            payload(pages), document_id=uuid4(), title=title, parser_version="1.28.2"
        )

    def test_plain_text_document_valid_and_ordered(self) -> None:
        ir = self.minimal(
            [
                page(
                    [
                        line("Title Line", y=72, size=16),
                        line("Body one.", y=100),
                        line("Body two.", y=114),
                    ]
                )
            ]
        )
        result = validate_document_ir(ir)
        assert result.ok, [i.message for i in result.issues]
        kinds = [e.kind for e in ir.elements]
        assert kinds[0] == "heading"
        assert "paragraph" in kinds
        body = [e for e in ir.elements if e.kind == "paragraph"]
        assert len(body) == 1  # merged
        assert ir.quality.reading_order_confidence == pytest.approx(1.0)

    def test_sibling_headings_replace_path(self) -> None:
        ir = self.minimal(
            [
                page(
                    [
                        line("Intro", y=72, size=16),
                        line("Text under intro.", y=100),
                        line("Method", y=140, size=16),
                        line("Text under method.", y=168),
                    ]
                )
            ]
        )
        paragraphs = {e.raw_text.split("\n")[0]: e for e in ir.elements if e.kind == "paragraph"}
        assert paragraphs["Text under intro."].section_path == ["Intro"]
        # Same-size headings are siblings: the path replaces, not nests.
        assert paragraphs["Text under method."].section_path == ["Method"]

    def test_nested_heading_sizes_nest(self) -> None:
        ir = self.minimal(
            [
                page(
                    [
                        line("Intro", y=72, size=16),
                        line("Body text.", y=100),
                        line("Subsection", y=140, size=12),
                        line("Deep body.", y=168),
                    ]
                )
            ]
        )
        paragraphs = {e.raw_text.split("\n")[0]: e for e in ir.elements if e.kind == "paragraph"}
        assert paragraphs["Deep body."].section_path == ["Intro", "Subsection"]

    def test_table_element_created_and_bound(self) -> None:
        primitive = {
            "bbox": [72.0, 100.0, 372.0, 140.0],
            "row_count": 2,
            "col_count": 3,
            "rows_text": [["Model", "IS", "FID"], ["DDPM", "9.46±0.11", "3.17"]],
            "cell_bboxes": [
                [[72 + c * 100, 100, 172 + c * 100, 120] for c in range(3)] for _ in range(2)
            ],
            "geometry_verified": True,
        }
        ir = self.minimal(
            [
                page(
                    [
                        line("Results section", y=72, size=15),
                        line("Body line for baseline size.", y=200),
                    ],
                    tables=[primitive],
                )
            ]
        )
        tables = [e for e in ir.elements if e.kind == "table"]
        assert len(tables) == 1
        assert tables[0].table is not None
        assert tables[0].section_path == ["Results section"]
        result = validate_document_ir(ir)
        assert result.ok, [i.message for i in result.issues]

    def test_unicode_replacement_becomes_hard_failure(self) -> None:
        bad = line("broken \ufffd text here", y=100)
        ir = build_document_ir(
            payload([page([bad])]),
            document_id=uuid4(),
            title="T",
            parser_version="1.28.2",
            max_replacement_characters=0,
        )
        assert ir.quality.replacement_character_count >= 1
        assert any("replacement characters" in f for f in ir.quality.hard_failures)

    def test_orphan_overflow_hard_failure(self) -> None:
        primitive = {
            "bbox": [72.0, 100.0, 272.0, 140.0],
            "row_count": 2,
            "col_count": 2,
            "rows_text": [["", "IS"], ["", "9.46"]],
            "cell_bboxes": [
                [[72 + c * 100, 100, 172 + c * 100, 120] for c in range(2)] for _ in range(2)
            ],
            "geometry_verified": True,
        }
        ir = build_document_ir(
            payload([page([], tables=[primitive])]),
            document_id=uuid4(),
            title="T",
            parser_version="1.28.2",
            max_orphan_ratio=0.05,
        )
        assert any("orphan numeric ratio" in f for f in ir.quality.hard_failures)

    def test_multicolumn_page_confidence_below_one(self) -> None:
        left = [line(f"L{i}", y=80 + i * 30, x=60, x1=280) for i in range(8)]
        right = [line(f"R{i}", y=80 + i * 30, x=330, x1=550) for i in range(8)]
        blocks_payload = page(left + right)
        # split into per-column blocks so crossing stays zero
        blocks_payload["blocks"] = [
            {"bbox": [ln["bbox"][0], ln["bbox"][1], ln["bbox"][2], ln["bbox"][3]], "lines": [ln]}
            for ln in left + right
        ]
        ir = self.minimal([blocks_payload])
        assert ir.metadata["column_counts"]["1"] == 2
        assert ir.quality.reading_order_confidence < 1.0
        left_first = ir.elements[0].raw_text
        assert left_first.startswith("L")  # left column read before right

    def test_parser_manifest_signature_consistent(self) -> None:
        ir = self.minimal([page([line("hello world", y=100)])])
        assert ir.parser.parser_id == "pymupdf"
        assert ir.parser.options == {"ocr": False}
        assert len(ir.parser.signature) == 64


class TestOcrGate:
    def test_blank_pages_raise_ocr_required(self) -> None:
        from app.loaders.pymupdf_adapter import check_ocr_required

        empty = payload(
            [
                {"physical_page": n, "width": 612.0, "height": 792.0, "blocks": [], "tables": []}
                for n in range(1, 6)
            ]
        )
        with pytest.raises(ParseError) as exc_info:
            check_ocr_required(empty)
        assert exc_info.value.code == "OCR_REQUIRED"

    def test_normal_pages_pass(self) -> None:
        from app.loaders.pymupdf_adapter import check_ocr_required

        check_ocr_required(payload([page([line("plenty of text " * 5, y=100)])]))

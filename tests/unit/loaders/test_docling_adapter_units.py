"""Docling adapter conversion tests using deterministic fake payloads.

No docling import and no model downloads happen here (spec §18.1): the pure
builder consumes DoclingDocument-shaped dicts captured from the real export
format (docling 2.121.0).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.document_ir.errors import OCR_REQUIRED, PDF_TABLE_INVALID, ParseError
from app.document_ir.models import ParserManifest
from app.document_ir.serialize import compute_parser_signature
from app.document_ir.validate import validate_document_ir
from app.loaders.docling_adapter import (
    DoclingParser,
    build_document_ir_from_docling,
    build_table_from_docling,
    check_docling_content,
    merge_pymupdf_table_fallback,
)

PAGE_W, PAGE_H = 595.0, 842.0


# --- Fake payload builders ---------------------------------------------------


def bl_bbox(left: float, top: float, right: float, bottom: float) -> dict[str, Any]:
    return {"l": left, "t": top, "r": right, "b": bottom, "coord_origin": "BOTTOMLEFT"}


def prov(page_no: int, box: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [
        {
            "page_no": page_no,
            "bbox": box or bl_bbox(72.0, 700.0, 300.0, 690.0),
            "charspan": [0, 10],
        }
    ]


def text_item(
    position: int,
    *,
    label: str = "text",
    text: str = "body text",
    level: int | None = None,
    layer: str = "body",
    page_no: int = 1,
    extra_prov: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "self_ref": f"#/texts/{position}",
        "parent": {"$ref": "#/body"},
        "children": [],
        "content_layer": layer,
        "label": label,
        "prov": prov(page_no),
        "orig": text,
        "text": text,
    }
    if level is not None:
        item["level"] = level
    if extra_prov is not None:
        item["prov"] = extra_prov
    return item


def grid_cell(
    text: str,
    row: int,
    col: int,
    *,
    column_header: bool = False,
    row_header: bool = False,
    row_end: int | None = None,
    col_end: int | None = None,
) -> dict[str, Any]:
    return {
        "text": text,
        "column_header": column_header,
        "row_header": row_header,
        "row_section": False,
        "fillable": False,
        "start_row_offset_idx": row,
        "end_row_offset_idx": row_end if row_end is not None else row + 1,
        "start_col_offset_idx": col,
        "end_col_offset_idx": col_end if col_end is not None else col + 1,
        "bbox": bl_bbox(
            72.0 + col * 100.0, 600.0 - row * 30.0, 172.0 + col * 100.0, 570.0 - row * 30.0
        ),
    }


def table_item(
    position: int,
    grid: list[list[dict[str, Any]]],
    *,
    caption_ref: str | None = None,
    page_no: int = 1,
) -> dict[str, Any]:
    rows = len(grid)
    cols = len(grid[0]) if grid else 0
    item: dict[str, Any] = {
        "self_ref": f"#/tables/{position}",
        "parent": {"$ref": "#/body"},
        "children": [],
        "content_layer": "body",
        "label": "table",
        "prov": prov(page_no),
        "captions": [{"$ref": caption_ref}] if caption_ref else [],
        "annotations": [],
        "data": {"num_rows": rows, "num_cols": cols, "grid": grid},
    }
    return item


def payload(
    *,
    texts: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
    pictures: list[dict[str, Any]] | None = None,
    body_refs: list[str] | None = None,
    furniture_refs: list[str] | None = None,
    page_count: int = 1,
) -> dict[str, Any]:
    return {
        "schema_name": "DoclingDocument",
        "version": "1.10.0",
        "name": "fake",
        "texts": texts or [],
        "tables": tables or [],
        "pictures": pictures or [],
        "key_value_items": [],
        "form_items": [],
        "groups": [],
        "furniture": {
            "self_ref": "#/furniture",
            "children": [{"$ref": r} for r in (furniture_refs or [])],
            "content_layer": "furniture",
            "name": "_root_",
            "label": "unspecified",
        },
        "body": {
            "self_ref": "#/body",
            "children": [{"$ref": r} for r in (body_refs or [])],
            "content_layer": "body",
            "name": "_root_",
            "label": "unspecified",
        },
        "pages": {
            str(n): {"size": {"width": PAGE_W, "height": PAGE_H}, "page_no": n}
            for n in range(1, page_count + 1)
        },
    }


def manifest(version: str = "2.121.0") -> ParserManifest:
    model_ids = {"layout": "docling-project/docling-layout-heron"}
    model_revisions = {"layout": "0123456789abcdef"}
    options: dict[str, bool | int | float | str] = {"ocr": False, "table_structure": True}
    return ParserManifest(
        parser_id="docling",
        parser_version=version,
        model_ids=model_ids,
        model_revisions=model_revisions,
        options=options,
        signature=compute_parser_signature(
            parser_id="docling",
            parser_version=version,
            model_ids=model_ids,
            model_revisions=model_revisions,
            options=options,
        ),
    )


def build(fake: dict[str, Any]):
    return build_document_ir_from_docling(
        fake, document_id=uuid4(), title="Fake Paper", manifest=manifest()
    )


# --- Tests -------------------------------------------------------------------


class TestTextConversion:
    def test_nul_removed_from_title_and_text(self) -> None:
        texts = [text_item(0, text="raw\x00text")]
        ir = build_document_ir_from_docling(
            payload(texts=texts, body_refs=["#/texts/0"]),
            document_id=uuid4(),
            title="title\x00value",
            manifest=manifest(),
        )

        assert ir.title == "titlevalue"
        assert ir.elements[0].raw_text == "rawtext"
        assert not validate_document_ir(ir).issues

    def test_reading_order_and_kinds(self) -> None:
        texts = [
            text_item(0, label="section_header", text="Intro", level=1),
            text_item(1, text="First paragraph."),
            text_item(2, text="Second paragraph."),
        ]
        ir = build(payload(texts=texts, body_refs=["#/texts/0", "#/texts/1", "#/texts/2"]))
        kinds = [e.kind for e in ir.elements]
        assert kinds == ["heading", "paragraph", "paragraph"]
        assert [e.reading_order for e in ir.elements] == [0, 1, 2]

    def test_section_path_nesting(self) -> None:
        texts = [
            text_item(0, label="section_header", text="Methods", level=1),
            text_item(1, label="section_header", text="Setup", level=2),
            text_item(2, text="Under Setup."),
            text_item(3, label="section_header", text="Results", level=1),
            text_item(4, text="Under Results."),
        ]
        refs = [f"#/texts/{i}" for i in range(5)]
        ir = build(payload(texts=texts, body_refs=refs))
        assert ir.elements[0].section_path == []
        assert ir.elements[1].section_path == ["Methods"]
        assert ir.elements[2].section_path == ["Methods", "Setup"]
        assert ir.elements[3].section_path == []
        assert ir.elements[4].section_path == ["Results"]

    def test_title_has_empty_section_path(self) -> None:
        texts = [
            text_item(0, label="title", text="A Paper Title"),
            text_item(1, text="Abstract body."),
        ]
        ir = build(payload(texts=texts, body_refs=["#/texts/0", "#/texts/1"]))
        assert ir.elements[0].kind == "title"
        assert ir.elements[0].section_path == []

    def test_furniture_emitted_before_body_as_footer(self) -> None:
        texts = [
            text_item(0, text="Page 1", label="page_footer", layer="furniture"),
            text_item(1, text="Body paragraph."),
        ]
        ir = build(
            payload(
                texts=texts,
                body_refs=["#/texts/1"],
                furniture_refs=["#/texts/0"],
            )
        )
        assert ir.elements[0].kind == "footer"
        assert ir.elements[0].reading_order == 0
        assert ir.elements[1].reading_order == 1

    def test_label_mapping(self) -> None:
        cases = [
            ("list_item", "list"),
            ("caption", "caption"),
            ("code", "code"),
            ("formula", "formula"),
            ("reference", "paragraph"),
            ("page_header", "header"),
            ("unknown_label", "paragraph"),
        ]
        texts = [text_item(i, label=label, text=f"t{i}") for i, (label, _) in enumerate(cases)]
        ir = build(payload(texts=texts, body_refs=[f"#/texts/{i}" for i in range(len(cases))]))
        got = [e.kind for e in ir.elements]
        assert got == [expected for _, expected in cases]

    def test_bbox_bottomleft_converted_to_top_left(self) -> None:
        texts = [text_item(0, text="x")]
        # t=778.616 (top), b=767.516 (bottom) on an 842pt page.
        texts[0]["prov"] = prov(1, bl_bbox(72.0, 778.616, 194.712, 767.516))
        ir = build(payload(texts=texts, body_refs=["#/texts/0"]))
        span = ir.elements[0].provenance[0]
        assert span.bbox is not None
        assert span.bbox.y0 == pytest.approx(PAGE_H - 778.616)
        assert span.bbox.y1 == pytest.approx(PAGE_H - 767.516)
        assert (span.bbox.x0, span.bbox.x1) == (72.0, 194.712)

    def test_bbox_clamped_into_page(self) -> None:
        texts = [text_item(0, text="x")]
        texts[0]["prov"] = prov(1, bl_bbox(-10.0, PAGE_H + 50.0, 900.0, -50.0))
        ir = build(payload(texts=texts, body_refs=["#/texts/0"]))
        span = ir.elements[0].provenance[0]
        assert span.bbox is not None
        assert span.bbox.x0 == 0.0
        assert span.bbox.x1 == PAGE_W
        assert span.bbox.y0 == 0.0
        assert span.bbox.y1 == PAGE_H

    def test_cross_page_paragraph_keeps_all_provenance(self) -> None:
        merged = "This paragraph spans pages one and two."
        texts = [
            text_item(
                0,
                text=merged,
                extra_prov=[
                    prov(1)[0],
                    {"page_no": 2, "bbox": bl_bbox(72.0, 700.0, 300.0, 650.0), "charspan": [0, 5]},
                ],
            )
        ]
        ir = build(payload(texts=texts, body_refs=["#/texts/0"], page_count=2))
        pages = sorted(span.physical_page for span in ir.elements[0].provenance)
        assert pages == [1, 2]


class TestTableConversion:
    def test_pymupdf_fallback_adds_table_on_page_missing_from_docling(self) -> None:
        primary = build(payload(texts=[text_item(0)], body_refs=["#/texts/0"]))
        grid = [
            [grid_cell("Model", 0, 0, column_header=True), grid_cell("FID", 0, 1)],
            [grid_cell("Ours", 1, 0), grid_cell("3.17", 1, 1)],
        ]
        fallback = build(payload(tables=[table_item(0, grid)], body_refs=["#/tables/0"]))

        merged = merge_pymupdf_table_fallback(primary, fallback)

        tables = [element for element in merged.elements if element.kind == "table"]
        assert len(tables) == 1
        assert tables[0].metadata["table_fallback"] == "pymupdf"
        assert merged.quality.table_count == 1
        assert merged.metadata["pymupdf_table_fallback_count"] == 1
        assert not validate_document_ir(merged).issues

    def test_pymupdf_fallback_skips_page_with_docling_table(self) -> None:
        primary_grid = [
            [grid_cell("Model", 0, 0, column_header=True), grid_cell("FID", 0, 1)],
            [grid_cell("Docling", 1, 0), grid_cell("3.17", 1, 1)],
        ]
        fallback_grid = [
            [grid_cell("Model", 0, 0, column_header=True), grid_cell("FID", 0, 1)],
            [grid_cell("Fallback", 1, 0), grid_cell("9.99", 1, 1)],
        ]
        primary = build(payload(tables=[table_item(0, primary_grid)], body_refs=["#/tables/0"]))
        fallback = build(payload(tables=[table_item(0, fallback_grid)], body_refs=["#/tables/0"]))

        merged = merge_pymupdf_table_fallback(primary, fallback)

        assert merged is primary
        assert "Docling" in merged.elements[0].raw_text
        assert "Fallback" not in merged.elements[0].raw_text

    def test_nul_removed_from_cell_and_caption(self) -> None:
        texts = [text_item(0, label="caption", text="cap\x00tion")]
        grid = [[grid_cell("head\x00er", 0, 0, column_header=True)]]
        ir = build(
            payload(
                texts=texts,
                tables=[table_item(0, grid, caption_ref="#/texts/0")],
                body_refs=["#/tables/0"],
            )
        )

        table = ir.elements[0].table
        assert table is not None
        assert table.caption == "caption"
        assert table.cells[0].raw_text == "header"
        assert not validate_document_ir(ir).issues

    def test_unflagged_real_payload_infers_headers(self) -> None:
        grid = [
            [grid_cell("Model", 0, 0), grid_cell("FID", 0, 1)],
            [grid_cell("DDPM", 1, 0), grid_cell("3.17", 1, 1)],
        ]
        built, warnings, _spans = build_table_from_docling(table_item(0, grid), {}, {})
        assert warnings == []
        assert built is not None
        assert [cell.normalized_text for cell in built.cells if cell.is_column_header] == [
            "Model",
            "FID",
        ]
        assert [cell.normalized_text for cell in built.cells if cell.is_row_header] == ["DDPM"]

    def test_simple_table_with_caption(self) -> None:
        texts = [text_item(0, label="caption", text="Table 1: Metrics")]
        grid = [
            [
                grid_cell("Model", 0, 0, column_header=True),
                grid_cell("IS", 0, 1, column_header=True),
                grid_cell("FID", 0, 2, column_header=True),
            ],
            [
                grid_cell("Ours", 1, 0, row_header=True),
                grid_cell("9.46", 1, 1),
                grid_cell("3.17", 1, 2),
            ],
        ]
        tables = [table_item(0, grid, caption_ref="#/texts/0")]
        ir = build(
            payload(
                texts=texts,
                tables=tables,
                body_refs=["#/tables/0"],
            )
        )
        assert len(ir.elements) == 1
        element = ir.elements[0]
        assert element.kind == "table"
        table = element.table
        assert table is not None
        assert (table.row_count, table.column_count) == (2, 3)
        assert table.header_rows == [0]
        assert table.caption == "Table 1: Metrics"
        assert "| Model | IS | FID |" in table.markdown
        assert "| Ours | 9.46 | 3.17 |" in table.markdown
        header_cells = [c for c in table.cells if c.is_column_header]
        assert len(header_cells) == 3
        row_headers = [c for c in table.cells if c.is_row_header]
        assert [c.normalized_text for c in row_headers] == ["Ours"]

    def test_merged_cell_single_entry_with_span(self) -> None:
        # Docling repeats the SAME cell object across covered grid positions;
        # the adapter must emit it once with its span, never as overlaps.
        merged = grid_cell("Group", 0, 0, column_header=True, col_end=2)
        grid = [
            [merged, merged],
            [grid_cell("a", 1, 0), grid_cell("1", 1, 1)],
        ]
        tables = [table_item(0, grid)]
        built, warnings, _spans = build_table_from_docling(tables[0], {}, {})
        assert warnings == []
        assert built is not None
        group_cells = [c for c in built.cells if c.normalized_text == "Group"]
        assert len(group_cells) == 1
        assert group_cells[0].column_span == 2
        assert len(built.cells) == 3

    def test_serialized_merged_cell_copies_are_deduplicated(self) -> None:
        # model_dump/json serialization destroys object identity while keeping
        # the same logical merged-cell coordinates in every covered slot.
        merged = grid_cell("Group", 0, 0, column_header=True, row_end=3)
        grid = [
            [dict(merged), grid_cell("A", 0, 1, column_header=True)],
            [dict(merged), grid_cell("1", 1, 1)],
            [dict(merged), grid_cell("2", 2, 1)],
        ]

        built, warnings, _spans = build_table_from_docling(table_item(0, grid), {}, {})

        assert warnings == []
        assert built is not None
        group_cells = [cell for cell in built.cells if cell.normalized_text == "Group"]
        assert len(group_cells) == 1
        assert group_cells[0].row_span == 3
        assert not validate_document_ir(
            build(payload(tables=[table_item(0, grid)], body_refs=["#/tables/0"]))
        ).issues

    def test_cell_provenance_bbox_present(self) -> None:
        grid = [[grid_cell("H", 0, 0, column_header=True)], [grid_cell("v", 1, 0)]]
        tables = [table_item(0, grid)]
        ir = build(payload(tables=tables, body_refs=["#/tables/0"]))
        table = ir.elements[0].table
        assert table is not None
        for cell in table.cells:
            assert cell.provenance, f"cell {cell.row},{cell.column} missing provenance"
            assert cell.provenance[0].bbox is not None

    def test_ragged_grid_rejected_not_fabricated(self) -> None:
        bad = table_item(0, [[grid_cell("a", 0, 0), grid_cell("b", 0, 1)]])
        bad["data"]["num_cols"] = 3  # declared wider than the grid
        ir = build(payload(tables=[bad], body_refs=["#/tables/0"]))
        assert ir.quality.malformed_table_count == 1
        assert ir.quality.table_count == 0
        assert not ir.elements
        assert any(PDF_TABLE_INVALID in w for w in ir.quality.warnings)


class TestQualityAndOrder:
    def test_dangling_reference_lowers_confidence(self) -> None:
        texts = [text_item(0, text="real content")]
        ir = build(payload(texts=texts, body_refs=["#/texts/0", "#/texts/42"]))
        assert len(ir.elements) == 1
        assert ir.quality.reading_order_confidence == pytest.approx(0.98)
        assert any("dangling" in w for w in ir.quality.warnings)

    def test_replacement_characters_counted(self) -> None:
        texts = [text_item(0, text="broken \ufffd char")]
        ir = build(payload(texts=texts, body_refs=["#/texts/0"]))
        assert ir.quality.replacement_character_count >= 1

    def test_orphan_ratio_zero_for_well_bound_table(self) -> None:
        grid = [
            [
                grid_cell("Model", 0, 0, column_header=True),
                grid_cell("FID", 0, 1, column_header=True),
            ],
            [grid_cell("Ours", 1, 0), grid_cell("3.17", 1, 1)],
        ]
        ir = build(payload(tables=[table_item(0, grid)], body_refs=["#/tables/0"]))
        assert ir.quality.orphan_numeric_ratio == 0.0

    def test_empty_document_hard_failure(self) -> None:
        ir = build(payload())
        assert ir.quality.hard_failures


class TestIRValidity:
    def test_fake_payload_produces_valid_ir(self) -> None:
        texts = [
            text_item(0, label="title", text="Deep Paper"),
            text_item(1, label="section_header", text="Experiments", level=1),
            text_item(2, text="We evaluate."),
        ]
        grid = [
            [grid_cell("M", 0, 0, column_header=True), grid_cell("V", 0, 1, column_header=True)],
            [grid_cell("ours", 1, 0), grid_cell("1.0", 1, 1)],
        ]
        ir = build(
            payload(
                texts=texts,
                tables=[table_item(0, grid)],
                body_refs=["#/texts/0", "#/texts/1", "#/tables/0", "#/texts/2"],
            )
        )
        result = validate_document_ir(ir)
        assert not result.issues, [i.message for i in result.issues]

    def test_json_round_trip_preserves_ir(self) -> None:
        texts = [text_item(0, label="section_header", text="S", level=1), text_item(1)]
        grid = [[grid_cell("H", 0, 0, column_header=True)], [grid_cell("v", 1, 0)]]
        ir = build(
            payload(texts=texts, tables=[table_item(0, grid)], body_refs=["#/texts/0", "#/texts/1"])
        )
        revived = type(ir).model_validate_json(ir.model_dump_json())
        assert revived.model_dump() == ir.model_dump()


class TestManifestAndHelpers:
    def test_signature_stable_for_same_options(self) -> None:
        kwargs: dict[str, Any] = {
            "parser_version": "2.121.0",
            "model_ids": {"layout": "m"},
            "options": {"ocr": False},
        }
        a = compute_parser_signature(parser_id="docling", **kwargs)
        b = compute_parser_signature(parser_id="docling", **dict(kwargs))
        assert a == b

    def test_signature_changes_with_options(self) -> None:
        base = {"parser_version": "2.121.0"}
        a = compute_parser_signature(parser_id="docling", options={"ocr": False}, **base)
        b = compute_parser_signature(parser_id="docling", options={"ocr": True}, **base)
        assert a != b

    def test_check_content_requires_text_layer(self) -> None:
        with pytest.raises(ParseError) as exc_info:
            check_docling_content({})
        assert exc_info.value.code == OCR_REQUIRED
        check_docling_content({"texts": [text_item(0)]})

    def test_derive_title_prefers_title_label(self) -> None:
        from app.loaders.docling_adapter import _derive_title

        fake = payload(
            texts=[text_item(0, text="not yet"), text_item(1, label="title", text="The Title")]
        )
        assert _derive_title(fake) == "The Title"

    def test_from_settings_maps_fields(self) -> None:
        class S:
            docling_ocr = False
            docling_table_structure = True
            docling_formula_enrichment = True
            docling_device = "cpu"
            docling_artifacts_path = ""
            docling_layout_model = "docling-project/docling-layout-heron"
            docling_table_model = "docling-project/TableFormer"
            docling_layout_revision = "abc123"
            docling_table_revision = ""

        parser = DoclingParser.from_settings(S())
        assert parser._options == {
            "ocr": False,
            "table_structure": True,
            "formula_enrichment": True,
            "pymupdf_table_fallback": True,
            "device": "cpu",
        }
        assert parser._model_ids == {
            "layout": "docling-project/docling-layout-heron",
            "table": "docling-project/TableFormer",
            "table_fallback": "pymupdf/find_tables",
        }
        assert parser._model_revisions["layout"] == "abc123"
        assert parser._model_revisions["table"] == ""
        assert parser._model_revisions["table_fallback"]

    def test_manifest_uses_override_version_without_docling(self) -> None:
        pinned = DoclingParser(parser_version="2.121.0", model_ids={"layout": "m"})
        m = pinned.manifest
        assert m.parser_id == "docling"
        assert m.parser_version == "2.121.0"
        assert len(m.signature) == 64

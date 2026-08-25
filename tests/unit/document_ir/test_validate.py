"""Tests for the Document IR validator (spec §5.3)."""

from __future__ import annotations

from uuid import uuid4

from app.document_ir.validate import ValidationIssue, ValidationResult, validate_document_ir
from tests.unit.document_ir.builders import (
    PAGE_WIDTH,
    make_bbox,
    make_cell,
    make_element,
    make_ir,
    make_manifest,
    make_quality,
    make_span,
    make_table_data,
)


def issue_codes(ir: object) -> list[str]:
    return [issue.code for issue in validate_document_ir(ir).issues]  # type: ignore[arg-type]


class TestValidMinimal:
    def test_minimal_valid_ir_passes(self) -> None:
        result = validate_document_ir(make_ir())
        assert result.ok, [i.message for i in result.issues]

    def test_multi_page_with_tables_passes(self) -> None:
        cells = [
            make_cell(0, 0, "H", is_column_header=True),
            make_cell(1, 0, "v"),
        ]
        table = make_table_data(cells, row_count=2, column_count=1, header_rows=[0])
        elements = [
            make_element(kind="title", text="T", reading_order=0),
            make_element(kind="paragraph", text="P", reading_order=1),
            make_element(kind="table", text="Table", reading_order=2, page=2, table=table),
        ]
        result = validate_document_ir(make_ir(elements, page_count=2))
        assert result.ok, [i.message for i in result.issues]


class TestPageSequence:
    def test_gap_rejected(self) -> None:
        ir = make_ir(page_count=1)
        ir.pages[0].physical_page = 3
        assert "PAGE_SEQUENCE" in issue_codes(ir)

    def test_duplicate_page_rejected(self) -> None:
        from app.document_ir.models import PageIR

        ir = make_ir()
        ir.pages.append(PageIR(physical_page=1, width=612, height=792))
        assert "PAGE_SEQUENCE" in issue_codes(ir)

    def test_page_not_starting_at_one(self) -> None:
        ir = make_ir()
        ir.pages[0].physical_page = 2
        assert "PAGE_SEQUENCE" in issue_codes(ir)


class TestReadingOrder:
    def test_duplicate_order_rejected(self) -> None:
        e1 = make_element(reading_order=0)
        e2 = make_element(text="second", reading_order=0)
        assert "READING_ORDER" in issue_codes(make_ir([e1, e2]))

    def test_non_contiguous_rejected(self) -> None:
        e1 = make_element(reading_order=0)
        e2 = make_element(text="second", reading_order=5)
        assert "READING_ORDER" in issue_codes(make_ir([e1, e2]))


class TestReferences:
    def test_dangling_page_reference(self) -> None:
        ir = make_ir()
        ir.pages[0].element_ids.append(uuid4())
        assert "ELEMENT_REF_MISSING" in issue_codes(ir)

    def test_duplicate_element_id(self) -> None:
        e1 = make_element()
        e2 = make_element(element_id=e1.id, reading_order=1)
        assert "ELEMENT_ID_DUPLICATE" in issue_codes(make_ir([e1, e2]))


class TestProvenance:
    def test_element_without_provenance_rejected(self) -> None:
        element = make_element(with_provenance=False)
        assert "PROVENANCE_MISSING" in issue_codes(make_ir([element]))


class TestBboxBounds:
    def test_within_bounds_ok(self) -> None:
        assert validate_document_ir(make_ir()).ok

    def test_slightly_over_tolerance_rejected(self) -> None:
        element = make_element()
        element.provenance[0] = make_span(bbox=make_bbox(x1=PAGE_WIDTH + 0.6))
        assert "BBOX_OUT_OF_BOUNDS" in issue_codes(make_ir([element]))

    def test_within_tolerance_ok(self) -> None:
        element = make_element()
        element.provenance[0] = make_span(bbox=make_bbox(x1=PAGE_WIDTH + 0.4))
        assert validate_document_ir(make_ir([element])).ok

    def test_negative_origin_rejected(self) -> None:
        element = make_element()
        element.provenance[0] = make_span(bbox=make_bbox(x0=-0.7))
        assert "BBOX_OUT_OF_BOUNDS" in issue_codes(make_ir([element]))


class TestParents:
    def test_missing_parent(self) -> None:
        e1 = make_element(parent_id=uuid4())
        assert "PARENT_MISSING" in issue_codes(make_ir([e1]))

    def test_self_parent_is_cycle(self) -> None:
        e1 = make_element()
        e1.parent_id = e1.id
        assert "PARENT_CYCLE" in issue_codes(make_ir([e1]))

    def test_two_node_cycle(self) -> None:
        e1 = make_element()
        e2 = make_element(text="child", reading_order=1)
        e1.parent_id = e2.id
        e2.parent_id = e1.id
        codes = issue_codes(make_ir([e1, e2]))
        assert "PARENT_CYCLE" in codes

    def test_valid_chain_ok(self) -> None:
        parent = make_element(kind="heading", text="Section")
        child = make_element(text="Body", reading_order=1, parent_id=parent.id)
        assert validate_document_ir(make_ir([parent, child])).ok


class TestTables:
    def _valid_table_element(self) -> object:
        cells = [
            make_cell(0, 0, "Model", is_column_header=True),
            make_cell(0, 1, "IS", is_column_header=True),
            make_cell(1, 0, "DDPM"),
            make_cell(1, 1, "9.46±0.11"),
        ]
        table = make_table_data(cells, row_count=2, column_count=2, header_rows=[0])
        return make_element(kind="table", text="Table 1", table=table)

    def test_valid_table_ok(self) -> None:
        element = self._valid_table_element()
        assert validate_document_ir(make_ir([element])).ok

    def test_markdown_mismatch_rejected(self) -> None:
        element = self._valid_table_element()  # type: ignore[assignment]
        element.table.markdown = "| wrong |"
        assert "TABLE_MARKDOWN_MISMATCH" in issue_codes(make_ir([element]))

    def test_cell_out_of_bounds(self) -> None:
        cells = [
            make_cell(0, 0, "A", is_column_header=True),
            make_cell(5, 5, "x"),
        ]
        table = make_table_data(cells, row_count=2, column_count=1, header_rows=[0])
        element = make_element(kind="table", text="t", table=table)
        # markdown was generated for a grid that tolerated overflow; force canonical
        element.table.markdown = ""
        codes = issue_codes(make_ir([element]))
        assert "TABLE_CELL_OUT_OF_BOUNDS" in codes

    def test_overlapping_cells_rejected(self) -> None:
        cells = [
            make_cell(0, 0, "A", is_column_header=True),
            make_cell(1, 0, "v1"),
            make_cell(1, 0, "v2"),
        ]
        table = make_table_data(cells, row_count=2, column_count=1, header_rows=[0])
        element = make_element(kind="table", text="t", table=table)
        element.table.markdown = ""
        assert "TABLE_OVERLAP" in issue_codes(make_ir([element]))

    def test_invalid_header_row(self) -> None:
        cells = [
            make_cell(0, 0, "A", is_column_header=True),
            make_cell(1, 0, "v"),
        ]
        table = make_table_data(cells, row_count=2, column_count=1, header_rows=[0])
        element = make_element(kind="table", text="t", table=table)
        element.table.header_rows = [9]
        assert "TABLE_HEADER_ROW_INVALID" in issue_codes(make_ir([element]))


class TestTextNul:
    def test_nul_in_element_text(self) -> None:
        element = make_element(text="bad\x00text")
        assert "TEXT_NUL" in issue_codes(make_ir([element]))

    def test_nul_in_title(self) -> None:
        ir = make_ir(title="bad\x00title")
        assert "TEXT_NUL" in issue_codes(ir)

    def test_nul_in_cell_text(self) -> None:
        cells = [
            make_cell(0, 0, "a\x00b", is_column_header=True),
            make_cell(1, 0, "v"),
        ]
        table = make_table_data(cells, row_count=2, column_count=1, header_rows=[0])
        element = make_element(kind="table", text="t", table=table)
        element.table.markdown = ""
        assert "TEXT_NUL" in issue_codes(make_ir([element]))


class TestParserChecks:
    def test_unknown_revision_rejected(self) -> None:
        manifest = make_manifest(model_revisions={"layout": "unknown"})
        assert "REVISION_UNKNOWN" in issue_codes(make_ir(manifest=manifest))

    def test_empty_revision_rejected(self) -> None:
        manifest = make_manifest(model_revisions={"layout": "  "})
        assert "REVISION_UNKNOWN" in issue_codes(make_ir(manifest=manifest))

    def test_empty_parser_version_rejected(self) -> None:
        manifest = make_manifest(parser_version="")
        assert "REVISION_EMPTY" in issue_codes(make_ir(manifest=manifest))

    def test_tampered_signature_rejected(self) -> None:
        manifest = make_manifest()
        tampered = manifest.model_copy(update={"signature": "f" * 64})
        assert "SIGNATURE_MISMATCH" in issue_codes(make_ir(manifest=tampered))


class TestActivationGate:
    def test_hard_failures_block_activation(self) -> None:
        quality = make_quality(hard_failures=["layout collapsed"])
        assert "HARD_FAILURES_PRESENT" in issue_codes(make_ir(quality=quality))


class TestResultShape:
    def test_issue_has_code_and_message(self) -> None:
        ir = make_ir(title="x\x00y")
        result = validate_document_ir(ir)
        assert not result.ok
        issue = result.issues[0]
        assert issue.code == "TEXT_NUL"
        assert issue.message

    def test_ok_property(self) -> None:
        empty = ValidationResult(issues=[])
        populated = ValidationResult(issues=[ValidationIssue(code="X", message="m")])
        assert empty.ok is True
        assert populated.ok is False

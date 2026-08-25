"""Tests for Document IR data models (spec §5)."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.document_ir.models import (
    BoundingBox,
    DocumentElement,
    DocumentIR,
    PageIR,
    ParserManifest,
    SourceSpan,
    TableCell,
)
from tests.unit.document_ir.builders import (
    make_cell,
    make_element,
    make_ir,
    make_manifest,
    make_quality,
    make_span,
    make_table_data,
)


class TestBoundingBox:
    def test_accepts_valid_order(self) -> None:
        box = BoundingBox(x0=0, y0=0, x1=10, y1=20)
        assert box.x1 == 10

    def test_allows_zero_size(self) -> None:
        BoundingBox(x0=5, y0=5, x1=5, y1=5)

    def test_rejects_inverted_x(self) -> None:
        with pytest.raises(ValidationError, match="inverted"):
            BoundingBox(x0=10, y0=0, x1=5, y1=20)

    def test_rejects_inverted_y(self) -> None:
        with pytest.raises(ValidationError, match="inverted"):
            BoundingBox(x0=0, y0=30, x1=10, y1=20)

    def test_rejects_nan(self) -> None:
        with pytest.raises(ValidationError):
            BoundingBox(x0=float("nan"), y0=0, x1=1, y1=1)


class TestExtraForbid:
    def test_element_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            DocumentElement(
                id=uuid.uuid4(),
                kind="paragraph",
                reading_order=0,
                raw_text="t",
                normalized_text="t",
                provenance=[make_span()],
                bogus_field=True,
            )

    def test_span_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            SourceSpan(physical_page=1, unknown="x")

    def test_ir_rejects_unknown_top_level_field(self) -> None:
        ir = make_ir()
        payload = ir.model_dump(mode="json")
        payload["unexpected"] = 1
        with pytest.raises(ValidationError):
            DocumentIR.model_validate(payload)


class TestSchemaVersion:
    def test_defaults_to_two(self) -> None:
        assert make_ir().schema_version == 2

    def test_rejects_other_versions(self) -> None:
        ir = make_ir()
        payload = ir.model_dump(mode="json")
        payload["schema_version"] = 3
        with pytest.raises(ValidationError):
            DocumentIR.model_validate(payload)


class TestUuid4:
    def test_valid_uuid4_accepted(self) -> None:
        element = make_element()
        assert element.id.version == 4

    def test_element_id_must_be_uuid4(self) -> None:
        with pytest.raises(ValidationError, match="UUIDv4"):
            DocumentElement(
                id=uuid.UUID(int=1, version=1),
                kind="paragraph",
                reading_order=0,
                raw_text="t",
                normalized_text="t",
                provenance=[make_span()],
            )

    def test_cell_id_must_be_uuid4(self) -> None:
        with pytest.raises(ValidationError, match="UUIDv4"):
            TableCell(
                id=uuid.UUID(int=2, version=1),
                row=0,
                column=0,
                raw_text="t",
                normalized_text="t",
            )

    def test_document_id_must_be_uuid4(self) -> None:
        ir = make_ir()
        payload = ir.model_dump(mode="json")
        payload["document_id"] = str(uuid.uuid1())
        with pytest.raises(ValidationError, match="UUIDv4"):
            DocumentIR.model_validate(payload)


class TestTableBinding:
    def test_table_kind_requires_table(self) -> None:
        with pytest.raises(ValidationError, match="requires table data"):
            make_element(kind="table", text="")

    def test_non_table_kind_forbids_table(self) -> None:
        cells = [
            make_cell(0, 0, "H", is_column_header=True),
            make_cell(1, 0, "v"),
        ]
        table = make_table_data(cells, row_count=2, column_count=1, header_rows=[0])
        with pytest.raises(ValidationError, match="only.*table|table.*only"):
            make_element(kind="paragraph", table=table)

    def test_table_kind_with_table_ok(self) -> None:
        cells = [
            make_cell(0, 0, "H", is_column_header=True),
            make_cell(1, 0, "v"),
        ]
        table = make_table_data(cells, row_count=2, column_count=1, header_rows=[0])
        element = make_element(kind="table", text="Table 1", table=table)
        assert element.table is not None


class TestManifest:
    def test_options_required(self) -> None:
        with pytest.raises(ValidationError):
            ParserManifest(parser_id="pymupdf", parser_version="1.28.2", signature="a" * 64)

    def test_parser_id_literal(self) -> None:
        with pytest.raises(ValidationError):
            ParserManifest(
                parser_id="not-a-parser", parser_version="1.0", options={}, signature="a" * 64
            )

    def test_valid_manifest_round_trip(self) -> None:
        manifest = make_manifest()
        restored = ParserManifest.model_validate_json(manifest.model_dump_json())
        assert restored == manifest


class TestRoundTripModels:
    def test_full_ir_round_trip(self) -> None:
        ir = make_ir(
            elements=[
                make_element(kind="title", text="Title", reading_order=0),
                make_element(kind="paragraph", text="Body", reading_order=1),
            ]
        )
        restored = DocumentIR.model_validate_json(ir.model_dump_json())
        assert restored == ir

    def test_page_ir_round_trip(self) -> None:
        page = PageIR(physical_page=1, width=612, height=792, element_ids=[uuid.uuid4()])
        restored = PageIR.model_validate_json(page.model_dump_json())
        assert restored == page

    def test_quality_defaults(self) -> None:
        quality = make_quality()
        assert quality.hard_failures == []
        assert quality.warnings == []

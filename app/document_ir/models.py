"""Canonical Document IR data models (spec §5).

Pydantic v2, ``extra="forbid"``, UUIDv4 ids, bbox in PDF points with
top-left origin, physical pages 1-based, reading order 0-based and unique.
All text must be JSON-serializable; NUL is rejected at validation time.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class IRModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def _ensure_uuid4(value: UUID) -> UUID:
    if value.version != 4:
        raise ValueError("IR ids must be UUIDv4")
    return value


class BoundingBox(IRModel):
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def validate_order(self) -> BoundingBox:
        if self.x0 > self.x1 or self.y0 > self.y1:
            raise ValueError("bbox coordinates are inverted")
        return self


class SourceSpan(IRModel):
    physical_page: int = Field(ge=1)
    printed_page: str | None = None
    bbox: BoundingBox | None = None
    parser_element_id: str | None = None


ElementKind = Literal[
    "title",
    "heading",
    "paragraph",
    "list",
    "table",
    "formula",
    "figure",
    "caption",
    "header",
    "footer",
    "code",
]


class TableCell(IRModel):
    id: UUID = Field(default_factory=uuid4)
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)
    raw_text: str
    normalized_text: str
    is_column_header: bool = False
    is_row_header: bool = False
    provenance: list[SourceSpan] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _uuid4(cls, value: UUID) -> UUID:
        return _ensure_uuid4(value)

    @property
    def is_header(self) -> bool:
        return self.is_column_header or self.is_row_header


class TableData(IRModel):
    caption: str | None = None
    row_count: int = Field(ge=1)
    column_count: int = Field(ge=1)
    header_rows: list[int]
    cells: list[TableCell]
    markdown: str
    html: str | None = None


class DocumentElement(IRModel):
    id: UUID = Field(default_factory=uuid4)
    kind: ElementKind
    reading_order: int = Field(ge=0)
    raw_text: str
    normalized_text: str
    section_path: list[str] = Field(default_factory=list)
    provenance: list[SourceSpan] = Field(default_factory=list)
    parent_id: UUID | None = None
    table: TableData | None = None
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _uuid4(cls, value: UUID) -> UUID:
        return _ensure_uuid4(value)

    @model_validator(mode="after")
    def validate_table_binding(self) -> DocumentElement:
        if self.kind == "table" and self.table is None:
            raise ValueError("kind='table' requires table data")
        if self.kind != "table" and self.table is not None:
            raise ValueError("table data is only allowed for kind='table'")
        return self


class PageIR(IRModel):
    physical_page: int = Field(ge=1)
    printed_page: str | None = None
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    element_ids: list[UUID] = Field(default_factory=list)


class ParserManifest(IRModel):
    parser_id: Literal["pymupdf", "docling", "mineru"]
    parser_version: str
    model_ids: dict[str, str] = Field(default_factory=dict)
    model_revisions: dict[str, str] = Field(default_factory=dict)
    options: dict[str, bool | int | float | str]
    signature: str


class LayoutQualityReport(IRModel):
    replacement_character_count: int = Field(ge=0)
    broken_unicode_count: int = Field(ge=0)
    table_count: int = Field(ge=0)
    malformed_table_count: int = Field(ge=0)
    orphan_numeric_ratio: float = Field(ge=0, le=1)
    repeated_header_footer_ratio: float = Field(ge=0, le=1)
    reading_order_confidence: float = Field(ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    hard_failures: list[str] = Field(default_factory=list)


class DocumentIR(IRModel):
    schema_version: Literal[2] = 2
    document_id: UUID = Field(default_factory=uuid4)
    title: str
    parser: ParserManifest
    pages: list[PageIR]
    elements: list[DocumentElement]
    quality: LayoutQualityReport
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("document_id")
    @classmethod
    def _uuid4(cls, value: UUID) -> UUID:
        return _ensure_uuid4(value)

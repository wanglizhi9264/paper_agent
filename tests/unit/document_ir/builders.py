"""Shared builders for constructing valid Document IR objects in tests."""

from __future__ import annotations

from uuid import UUID, uuid4

from app.document_ir.markdown import render_table_markdown
from app.document_ir.models import (
    BoundingBox,
    DocumentElement,
    DocumentIR,
    LayoutQualityReport,
    PageIR,
    ParserManifest,
    SourceSpan,
    TableCell,
    TableData,
)
from app.document_ir.serialize import compute_parser_signature

PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0


def make_bbox(
    x0: float = 10.0, y0: float = 10.0, x1: float = 200.0, y1: float = 30.0
) -> BoundingBox:
    return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)


def make_span(
    page: int = 1, bbox: BoundingBox | None = None, printed_page: str | None = None
) -> SourceSpan:
    return SourceSpan(
        physical_page=page,
        bbox=bbox if bbox is not None else make_bbox(),
        printed_page=printed_page,
    )


def make_cell(
    row: int,
    column: int,
    text: str,
    *,
    row_span: int = 1,
    column_span: int = 1,
    is_column_header: bool = False,
    is_row_header: bool = False,
    cell_id: UUID | None = None,
) -> TableCell:
    return TableCell(
        id=cell_id or uuid4(),
        row=row,
        column=column,
        row_span=row_span,
        column_span=column_span,
        raw_text=text,
        normalized_text=text,
        is_column_header=is_column_header,
        is_row_header=is_row_header,
        provenance=[make_span()],
    )


def make_table_data(
    cells: list[TableCell],
    *,
    row_count: int,
    column_count: int,
    header_rows: list[int],
    caption: str | None = None,
) -> TableData:
    markdown = render_table_markdown(
        cells, row_count=row_count, column_count=column_count, header_rows=header_rows
    )
    return TableData(
        caption=caption,
        row_count=row_count,
        column_count=column_count,
        header_rows=header_rows,
        cells=cells,
        markdown=markdown,
    )


def make_element(
    kind: str = "paragraph",
    text: str = "Sample paragraph text.",
    *,
    reading_order: int = 0,
    element_id: UUID | None = None,
    page: int = 1,
    with_provenance: bool = True,
    parent_id: UUID | None = None,
    table: TableData | None = None,
    section_path: list[str] | None = None,
) -> DocumentElement:
    return DocumentElement(
        id=element_id or uuid4(),
        kind=kind,
        reading_order=reading_order,
        raw_text=text,
        normalized_text=text,
        section_path=section_path or [],
        provenance=[make_span(page=page)] if with_provenance else [],
        parent_id=parent_id,
        table=table,
    )


def make_manifest(
    parser_id: str = "pymupdf",
    parser_version: str = "1.28.2",
    model_revisions: dict[str, str] | None = None,
    options: dict[str, bool | int | float | str] | None = None,
) -> ParserManifest:
    revisions = model_revisions if model_revisions is not None else {"layout": "abc123"}
    opts = options if options is not None else {"ocr": False}
    signature = compute_parser_signature(
        parser_id=parser_id,
        parser_version=parser_version,
        model_ids={"layout": "layout-model"} if revisions else {},
        model_revisions=revisions,
        options=opts,
    )
    return ParserManifest(
        parser_id=parser_id,
        parser_version=parser_version,
        model_ids={"layout": "layout-model"} if revisions else {},
        model_revisions=revisions,
        options=opts,
        signature=signature,
    )


def make_quality(**overrides: object) -> LayoutQualityReport:
    values: dict[str, object] = {
        "replacement_character_count": 0,
        "broken_unicode_count": 0,
        "table_count": 0,
        "malformed_table_count": 0,
        "orphan_numeric_ratio": 0.0,
        "repeated_header_footer_ratio": 0.0,
        "reading_order_confidence": 1.0,
        "warnings": [],
        "hard_failures": [],
    }
    values.update(overrides)
    return LayoutQualityReport.model_validate(values)


def make_ir(
    elements: list[DocumentElement] | None = None,
    *,
    page_count: int = 1,
    title: str = "Test Document",
    manifest: ParserManifest | None = None,
    quality: LayoutQualityReport | None = None,
    document_id: UUID | None = None,
) -> DocumentIR:
    elements = elements if elements is not None else [make_element()]
    pages: list[PageIR] = []
    for number in range(1, page_count + 1):
        page_elements = [element for element in elements if _element_on_page(element, number)]
        pages.append(
            PageIR(
                physical_page=number,
                width=PAGE_WIDTH,
                height=PAGE_HEIGHT,
                element_ids=[element.id for element in page_elements],
            )
        )
    return DocumentIR(
        document_id=document_id or uuid4(),
        title=title,
        parser=manifest or make_manifest(),
        pages=pages,
        elements=elements,
        quality=quality or make_quality(),
    )


def _element_on_page(element: DocumentElement, page: int) -> bool:
    return any(span.physical_page == page for span in element.provenance)

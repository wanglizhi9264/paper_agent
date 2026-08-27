"""IR-native table parent/row/group chunking for PDF Ingestion V2-5."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from app.chunking.models import ChunkConfig, ChunkResult
from app.document_ir.markdown import render_table_grid, table_fingerprint
from app.document_ir.models import DocumentElement, SourceSpan, TableCell, TableData


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _span_bbox(span: SourceSpan) -> dict[str, float | int] | None:
    if span.bbox is None:
        return None
    return {
        "physical_page": span.physical_page,
        "x0": span.bbox.x0,
        "y0": span.bbox.y0,
        "x1": span.bbox.x1,
        "y1": span.bbox.y1,
    }


def _provenance_metadata(spans: list[SourceSpan]) -> tuple[list[int], list[dict[str, float | int]]]:
    pages = sorted({span.physical_page for span in spans})
    boxes = [box for span in spans if (box := _span_bbox(span)) is not None]
    return pages, boxes


def _covers(cell: TableCell, row: int, column: int) -> bool:
    return (
        cell.row <= row < cell.row + cell.row_span
        and cell.column <= column < cell.column + cell.column_span
    )


def _header_path(table: TableData, column: int) -> list[str]:
    path: list[str] = []
    for header_row in sorted(table.header_rows):
        candidates = [
            cell
            for cell in table.cells
            if cell.is_column_header and _covers(cell, header_row, column)
        ]
        for cell in sorted(candidates, key=lambda value: (value.row, value.column)):
            text = cell.normalized_text.strip()
            if text and (not path or path[-1] != text):
                path.append(text)
    return path or [f"column_{column}"]


def _row_cells(table: TableData, row: int) -> list[TableCell]:
    return sorted([cell for cell in table.cells if cell.row == row], key=lambda cell: cell.column)


def _row_headers(cells: list[TableCell]) -> list[TableCell]:
    explicit = [cell for cell in cells if cell.is_row_header]
    if explicit:
        return explicit
    return [cells[0]] if cells else []


def _row_markdown(row_label: str, values: list[tuple[list[str], TableCell]]) -> str:
    headers = ["Row", *[" > ".join(path) for path, _cell in values]]
    row = [row_label, *[cell.raw_text.strip() for _path, cell in values]]
    return (
        "| "
        + " | ".join(headers)
        + " |\n| "
        + " | ".join(["---"] * len(headers))
        + " |\n| "
        + " | ".join(row)
        + " |"
    )


def _retrieval_text(
    *,
    document_title: str,
    section_path: list[str],
    table_label: str,
    row_label: str,
    values: list[tuple[list[str], TableCell]],
) -> str:
    lines = [
        f"Document: {document_title}",
        f"Section: {' > '.join(section_path)}",
        f"Table: {table_label}",
        f"Row: {row_label}",
    ]
    lines.extend(f"{' > '.join(path)}: {cell.normalized_text.strip()}" for path, cell in values)
    return "\n".join(lines)


def _base_metadata(
    element: DocumentElement,
    *,
    subtype: str,
    fingerprint: str,
    cells: list[TableCell],
) -> dict[str, Any]:
    spans = [span for cell in cells for span in cell.provenance] or list(element.provenance)
    pages, boxes = _provenance_metadata(spans)
    return {
        "ir_schema_version": 2,
        "element_id": str(element.id),
        "element_kind": "table",
        "chunk_subtype": subtype,
        "table_fingerprint": fingerprint,
        "physical_pages": pages,
        "bboxes": boxes,
        "cell_ids": [str(cell.id) for cell in cells],
    }


def chunk_table_element(
    element: DocumentElement,
    *,
    document_title: str,
    start_index: int,
    config: ChunkConfig,
) -> list[ChunkResult]:
    """Create one non-indexed parent plus row/group chunks for a table element."""
    if element.table is None:
        raise ValueError("chunk_table_element requires a table element")
    table = element.table
    fingerprint = table_fingerprint(table)
    table_label = table.caption or f"table-{fingerprint[:12]}"
    grid, _warnings = render_table_grid(
        table.cells,
        row_count=table.row_count,
        column_count=table.column_count,
        header_rows=table.header_rows,
    )
    header_lines = [" | ".join(grid[row]) for row in sorted(table.header_rows)]
    parent_raw = "\n".join(
        [
            f"Document: {document_title}",
            f"Section: {' > '.join(element.section_path)}",
            f"Table: {table_label}",
            "Headers:",
            *header_lines,
        ]
    )
    parent_pages, _parent_boxes = _provenance_metadata(element.provenance)
    parent = ChunkResult(
        chunk_index=start_index,
        kind="table",
        section_path=list(element.section_path),
        raw_content=parent_raw,
        retrieval_content=parent_raw,
        content_hash=_hash(parent_raw),
        character_count=len(parent_raw),
        page_start=min(parent_pages, default=None),
        page_end=max(parent_pages, default=None),
        metadata=_base_metadata(element, subtype="table_parent", fingerprint=fingerprint, cells=[]),
    )
    results = [parent]

    raw_table_text = element.metadata.get("raw_table_text")
    if isinstance(raw_table_text, str) and raw_table_text.strip():
        raw_alias = raw_table_text.strip()
        raw_retrieval = "\n".join(
            [
                f"Document: {document_title}",
                f"Section: {' > '.join(element.section_path)}",
                f"Table: {table_label}",
                raw_alias,
            ]
        )
        raw_metadata = _base_metadata(
            element,
            subtype="table_raw_text",
            fingerprint=fingerprint,
            cells=[],
        )
        raw_metadata["parent_chunk_index"] = parent.chunk_index
        results.append(
            ChunkResult(
                chunk_index=start_index + len(results),
                kind="table",
                section_path=list(element.section_path),
                raw_content=raw_alias,
                retrieval_content=raw_retrieval[: config.retrieval_content_max_chars],
                content_hash=_hash(raw_alias),
                character_count=len(raw_alias),
                page_start=min(parent_pages, default=None),
                page_end=max(parent_pages, default=None),
                parent_chunk_index=parent.chunk_index,
                metadata=raw_metadata,
            )
        )

    for row_index in range(table.row_count):
        if row_index in table.header_rows:
            continue
        cells = _row_cells(table, row_index)
        if not cells:
            continue
        row_header_cells = _row_headers(cells)
        row_header_ids = {cell.id for cell in row_header_cells}
        row_label = (
            " > ".join(
                cell.normalized_text.strip()
                for cell in row_header_cells
                if cell.normalized_text.strip()
            )
            or f"row_{row_index}"
        )
        values = [
            (_header_path(table, cell.column), cell)
            for cell in cells
            if cell.id not in row_header_ids and cell.normalized_text.strip()
        ]
        if not values:
            continue
        raw = _row_markdown(row_label, values)
        retrieval = _retrieval_text(
            document_title=document_title,
            section_path=element.section_path,
            table_label=table_label,
            row_label=row_label,
            values=values,
        )
        metadata = _base_metadata(
            element,
            subtype="table_row",
            fingerprint=fingerprint,
            cells=[*row_header_cells, *[cell for _path, cell in values]],
        )
        metadata.update(
            {
                "parent_chunk_index": parent.chunk_index,
                "row_indices": [row_index],
                "row_header_path": [cell.normalized_text.strip() for cell in row_header_cells],
                "column_header_paths": [path for path, _cell in values],
                "overlong_row": len(retrieval) > config.max_chunk_chars,
            }
        )
        pages = metadata["physical_pages"]
        results.append(
            ChunkResult(
                chunk_index=start_index + len(results),
                kind="table",
                section_path=list(element.section_path),
                raw_content=raw,
                retrieval_content=retrieval[: config.retrieval_content_max_chars],
                content_hash=_hash(raw),
                character_count=len(raw),
                page_start=min(pages, default=None),
                page_end=max(pages, default=None),
                parent_chunk_index=parent.chunk_index,
                metadata=metadata,
            )
        )

        groups: dict[str, list[tuple[list[str], TableCell]]] = defaultdict(list)
        for path, cell in values:
            groups[path[0]].append((path, cell))
        needs_groups = (
            len(table.header_rows) > 1
            or len(retrieval) > config.max_chunk_chars
            or len(groups) < len(values)
            or len(row_header_cells) > 1
        )
        if not needs_groups:
            continue
        for group_name, group_values in groups.items():
            group_raw = _row_markdown(row_label, group_values)
            group_retrieval = _retrieval_text(
                document_title=document_title,
                section_path=element.section_path,
                table_label=table_label,
                row_label=row_label,
                values=group_values,
            )
            group_cells = [*row_header_cells, *[cell for _path, cell in group_values]]
            group_metadata = _base_metadata(
                element,
                subtype="table_group",
                fingerprint=fingerprint,
                cells=group_cells,
            )
            group_metadata.update(
                {
                    "parent_chunk_index": parent.chunk_index,
                    "row_indices": [row_index],
                    "row_header_path": [cell.normalized_text.strip() for cell in row_header_cells],
                    "column_header_paths": [path for path, _cell in group_values],
                    "header_group": group_name,
                }
            )
            group_pages = group_metadata["physical_pages"]
            results.append(
                ChunkResult(
                    chunk_index=start_index + len(results),
                    kind="table",
                    section_path=list(element.section_path),
                    raw_content=group_raw,
                    retrieval_content=group_retrieval[: config.retrieval_content_max_chars],
                    content_hash=_hash(group_raw),
                    character_count=len(group_raw),
                    page_start=min(group_pages, default=None),
                    page_end=max(group_pages, default=None),
                    parent_chunk_index=parent.chunk_index,
                    metadata=group_metadata,
                )
            )
    return results


__all__ = ["chunk_table_element"]

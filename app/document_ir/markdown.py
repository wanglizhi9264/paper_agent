"""Deterministic table Markdown rendering (spec §9.2).

Markdown is always generated from validated cells; parser-provided Markdown
is never trusted. Merged header cells expand their text across every covered
grid position; data cells keep their value only at the origin position.
Empty header cells get the stable placeholder ``column_<index>`` plus a
warning so the hard-case gate fails instead of guessing field names.
"""

from __future__ import annotations

import hashlib

from app.document_ir.models import TableCell, TableData


def _escape_cell_text(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_table_grid(
    cells: list[TableCell],
    *,
    row_count: int,
    column_count: int,
    header_rows: list[int],
) -> tuple[list[list[str]], list[str]]:
    """Expand cells onto a dense text grid.

    Returns ``(grid, warnings)``. Header cells repeat their text at every
    covered position; data cells write their value only at the origin
    position. Empty header cells receive the ``column_<index>`` placeholder.
    """
    grid: list[list[str]] = [["" for _ in range(column_count)] for _ in range(row_count)]
    warnings: list[str] = []
    owner: dict[tuple[int, int], TableCell] = {}

    for cell in sorted(cells, key=lambda c: (c.row, c.column)):
        is_header = cell.is_header
        text = _escape_cell_text(cell.normalized_text.strip())
        warned = f"empty-header:{cell.id}" in warnings
        for r in range(cell.row, cell.row + cell.row_span):
            for c in range(cell.column, cell.column + cell.column_span):
                previous = owner.get((r, c))
                if previous is not None and previous.id != cell.id:
                    continue
                owner[(r, c)] = cell
                if r >= row_count or c >= column_count:
                    continue
                if is_header:
                    if not text:
                        if not warned:
                            warnings.append(f"empty-header:{cell.id}")
                            warned = True
                        grid[r][c] = f"column_{c}"
                    else:
                        grid[r][c] = text
                elif (r, c) == (cell.row, cell.column):
                    grid[r][c] = text
    return grid, warnings


def render_table_markdown(
    cells: list[TableCell],
    *,
    row_count: int,
    column_count: int,
    header_rows: list[int],
) -> str:
    """Render canonical Markdown from cells (spec §9.2).

    The separator row is emitted after the last header row (row 0 when no
    header rows exist). Output ends with a single trailing newline.
    """
    grid, _warnings = render_table_grid(
        cells, row_count=row_count, column_count=column_count, header_rows=header_rows
    )
    separator_after = max(header_rows) if header_rows else 0
    lines: list[str] = []
    separator_emitted = False
    for r, row in enumerate(grid):
        lines.append("| " + " | ".join(row) + " |")
        if r == separator_after and not separator_emitted:
            lines.append("| " + " | ".join(["---"] * column_count) + " |")
            separator_emitted = True
    return "\n".join(lines)


def make_table_data(
    cells: list[TableCell],
    *,
    row_count: int,
    column_count: int,
    header_rows: list[int],
    caption: str | None = None,
    html: str | None = None,
) -> TableData:
    """Build a :class:`TableData` with deterministically generated Markdown."""
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
        html=html,
    )


def table_fingerprint(table: TableData) -> str:
    """Stable SHA-256 over caption + canonical grids (spec §9.3).

    Used for A/B comparison and duplicate detection only — never a business id.
    """
    grid, _warnings = render_table_grid(
        table.cells,
        row_count=table.row_count,
        column_count=table.column_count,
        header_rows=table.header_rows,
    )
    payload = {
        "caption": table.caption or "",
        "header_rows": sorted(table.header_rows),
        "row_count": table.row_count,
        "column_count": table.column_count,
        "grid": grid,
    }
    canonical = repr(payload).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()

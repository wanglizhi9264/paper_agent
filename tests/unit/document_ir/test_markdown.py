"""Tests for deterministic table Markdown (spec §9.2, §9.3)."""

from __future__ import annotations

import pytest

from app.document_ir.markdown import (
    make_table_data,
    render_table_grid,
    render_table_markdown,
    table_fingerprint,
)
from tests.unit.document_ir.builders import make_cell


class TestSimpleTable:
    def test_basic_rendering(self) -> None:
        cells = [
            make_cell(0, 0, "Model", is_column_header=True),
            make_cell(0, 1, "FID", is_column_header=True),
            make_cell(1, 0, "DDPM"),
            make_cell(1, 1, "3.17"),
        ]
        markdown = render_table_markdown(cells, row_count=2, column_count=2, header_rows=[0])
        assert markdown == "| Model | FID |\n| --- | --- |\n| DDPM | 3.17 |"

    def test_deterministic(self) -> None:
        cells = [
            make_cell(0, 0, "A", is_column_header=True),
            make_cell(1, 0, "1"),
        ]
        first = render_table_markdown(cells, row_count=2, column_count=1, header_rows=[0])
        second = render_table_markdown(cells, row_count=2, column_count=1, header_rows=[0])
        assert first == second


class TestMultiHeader:
    def test_two_level_headers_expand(self) -> None:
        cells = [
            make_cell(0, 0, "Method", is_column_header=True, row_span=2),
            make_cell(0, 1, "HumanAct12", is_column_header=True, column_span=2),
            make_cell(0, 3, "UESTC", is_column_header=True, column_span=2),
            make_cell(1, 1, "FIDtr", is_column_header=True),
            make_cell(1, 2, "Acc", is_column_header=True),
            make_cell(1, 3, "FID", is_column_header=True),
            make_cell(1, 4, "Acc", is_column_header=True),
            make_cell(2, 0, "ACTOR"),
            make_cell(2, 1, "0.12"),
            make_cell(2, 2, "0.85"),
            make_cell(2, 3, "0.15"),
            make_cell(2, 4, "0.82"),
        ]
        markdown = render_table_markdown(cells, row_count=3, column_count=5, header_rows=[0, 1])
        expected = (
            "| Method | HumanAct12 | HumanAct12 | UESTC | UESTC |\n"
            "| Method | FIDtr | Acc | FID | Acc |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| ACTOR | 0.12 | 0.85 | 0.15 | 0.82 |"
        )
        assert markdown == expected


class TestMergedCells:
    def test_data_cell_span_keeps_value_at_origin_only(self) -> None:
        cells = [
            make_cell(0, 0, "H", is_column_header=True),
            make_cell(0, 1, "G", is_column_header=True),
            make_cell(1, 0, "x"),
            make_cell(1, 1, "v", column_span=2),
        ]
        markdown = render_table_markdown(cells, row_count=2, column_count=3, header_rows=[0])
        assert markdown == "| H | G |  |\n| --- | --- | --- |\n| x | v |  |"

    def test_row_header_expands_across_rows(self) -> None:
        cells = [
            make_cell(0, 0, "C", is_column_header=True),
            make_cell(1, 0, "R", is_row_header=True, row_span=2),
        ]
        markdown = render_table_markdown(cells, row_count=3, column_count=1, header_rows=[0])
        assert markdown == "| C |\n| --- |\n| R |\n| R |"


class TestEmptyHeaders:
    def test_empty_header_gets_placeholder_and_warning(self) -> None:
        cells = [
            make_cell(0, 0, "", is_column_header=True),
            make_cell(1, 0, "v"),
        ]
        grid, warnings = render_table_grid(cells, row_count=2, column_count=1, header_rows=[0])
        assert grid[0][0] == "column_0"
        assert len(warnings) == 1

    def test_placeholder_in_markdown(self) -> None:
        cells = [
            make_cell(0, 1, "", is_column_header=True),
            make_cell(1, 0, "a"),
            make_cell(1, 1, "b"),
        ]
        markdown = render_table_markdown(cells, row_count=2, column_count=2, header_rows=[0])
        assert "column_1" in markdown


class TestEscaping:
    def test_pipe_escaped(self) -> None:
        cells = [
            make_cell(0, 0, "a|b", is_column_header=True),
            make_cell(1, 0, "c"),
        ]
        markdown = render_table_markdown(cells, row_count=2, column_count=1, header_rows=[0])
        assert "a\\|b" in markdown


class TestMakeTableData:
    def test_helper_computes_markdown(self) -> None:
        cells = [
            make_cell(0, 0, "K", is_column_header=True),
            make_cell(1, 0, "1"),
        ]
        table = make_table_data(cells, row_count=2, column_count=1, header_rows=[0], caption="T")
        assert table.markdown == render_table_markdown(
            cells, row_count=2, column_count=1, header_rows=[0]
        )
        assert table.caption == "T"


class TestFingerprint:
    def test_stable_for_same_content(self) -> None:
        cells = [
            make_cell(0, 0, "K", is_column_header=True),
            make_cell(1, 0, "9.46±0.11"),
        ]
        t1 = make_table_data(cells, row_count=2, column_count=1, header_rows=[0])
        t2 = make_table_data(cells, row_count=2, column_count=1, header_rows=[0])
        assert table_fingerprint(t1) == table_fingerprint(t2)

    def test_changes_with_content(self) -> None:
        base_cells = [
            make_cell(0, 0, "K", is_column_header=True),
            make_cell(1, 0, "1"),
        ]
        other_cells = [
            make_cell(0, 0, "K", is_column_header=True),
            make_cell(1, 0, "2"),
        ]
        t1 = make_table_data(base_cells, row_count=2, column_count=1, header_rows=[0])
        t2 = make_table_data(other_cells, row_count=2, column_count=1, header_rows=[0])
        assert table_fingerprint(t1) != table_fingerprint(t2)


class TestSeparatorPlacement:
    @pytest.mark.parametrize("header_rows", [[0], [0, 1], []])
    def test_separator_after_last_header(self, header_rows: list[int]) -> None:
        cells = [
            make_cell(r, c, f"h{r}{c}" if r == 0 else f"d{r}{c}", is_column_header=r == 0)
            for r in range(3)
            for c in range(2)
        ]
        markdown = render_table_markdown(
            cells, row_count=3, column_count=2, header_rows=header_rows
        )
        lines = markdown.split("\n")
        separator_index = next(i for i, line in enumerate(lines) if set(line) <= {"|", "-", " "})
        last_header = max(header_rows) if header_rows else 0
        assert separator_index == last_header + 1

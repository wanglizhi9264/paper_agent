from __future__ import annotations

from uuid import uuid4

import pytest

from app.chunking.models import ChunkConfig
from app.chunking.pipeline import chunk_document_ir
from app.document_ir.markdown import make_table_data
from app.document_ir.models import DocumentElement, SourceSpan, TableCell
from tests.unit.document_ir.builders import make_ir


def _cell(
    row: int,
    column: int,
    text: str,
    *,
    column_header: bool = False,
    row_header: bool = False,
    column_span: int = 1,
) -> TableCell:
    return TableCell(
        row=row,
        column=column,
        column_span=column_span,
        raw_text=text,
        normalized_text=text,
        is_column_header=column_header,
        is_row_header=row_header,
        provenance=[SourceSpan(physical_page=5)],
    )


def _multi_header_table() -> DocumentElement:
    cells = [
        _cell(0, 0, "Method", column_header=True, row_header=True),
        _cell(0, 1, "ImageNet-4", column_header=True, column_span=2),
        _cell(0, 3, "ImageNet-40", column_header=True, column_span=2),
        _cell(1, 1, "IS", column_header=True),
        _cell(1, 2, "FID", column_header=True),
        _cell(1, 3, "IS", column_header=True),
        _cell(1, 4, "FID", column_header=True),
        _cell(2, 0, "H", row_header=True),
        _cell(2, 1, "8.1"),
        _cell(2, 2, "4.2"),
        _cell(2, 3, "9.0"),
        _cell(2, 4, "3.8"),
        _cell(3, 0, "H+L+FiLM", row_header=True),
        _cell(3, 1, "8.9"),
        _cell(3, 2, "3.7"),
        _cell(3, 3, "9.8"),
        _cell(3, 4, "3.1"),
    ]
    table = make_table_data(
        cells,
        row_count=4,
        column_count=5,
        header_rows=[0, 1],
        caption="Table 2",
    )
    return DocumentElement(
        id=uuid4(),
        kind="table",
        reading_order=0,
        raw_text=table.markdown,
        normalized_text=table.markdown,
        section_path=["Experiments"],
        provenance=[SourceSpan(physical_page=5)],
        table=table,
    )


def test_table_parent_row_group_contract_and_binding() -> None:
    element = _multi_header_table()
    chunks = chunk_document_ir(make_ir(elements=[element]), ChunkConfig(max_chunk_chars=800))

    parent = next(chunk for chunk in chunks if chunk.metadata["chunk_subtype"] == "table_parent")
    rows = [chunk for chunk in chunks if chunk.metadata["chunk_subtype"] == "table_row"]
    groups = [chunk for chunk in chunks if chunk.metadata["chunk_subtype"] == "table_group"]

    assert parent.add_to_index is False
    assert parent.parent_chunk_index is None
    assert len(rows) == 2
    assert all(row.parent_chunk_index == parent.chunk_index for row in rows)
    film = next(row for row in rows if "H+L+FiLM" in row.retrieval_content)
    assert "ImageNet-4 > IS: 8.9" in film.retrieval_content
    assert "ImageNet-4 > FID: 3.7" in film.retrieval_content
    assert "ImageNet-40 > IS: 9.8" in film.retrieval_content
    assert film.metadata["column_header_paths"] == [
        ["ImageNet-4", "IS"],
        ["ImageNet-4", "FID"],
        ["ImageNet-40", "IS"],
        ["ImageNet-40", "FID"],
    ]
    assert len(groups) == 4
    assert {group.metadata["header_group"] for group in groups} == {
        "ImageNet-4",
        "ImageNet-40",
    }


def test_table_metadata_contains_element_cells_pages_and_bboxes() -> None:
    chunks = chunk_document_ir(make_ir(elements=[_multi_header_table()]))
    row = next(chunk for chunk in chunks if chunk.metadata["chunk_subtype"] == "table_row")
    assert row.metadata["element_id"]
    assert row.metadata["cell_ids"]
    assert row.metadata["physical_pages"] == [5]
    assert "bboxes" in row.metadata
    assert row.metadata["table_fingerprint"]
    assert row.raw_content.startswith("| Row |")


def test_table_chunking_is_deterministic_except_business_ids() -> None:
    ir = make_ir(elements=[_multi_header_table()])
    first = chunk_document_ir(ir)
    second = chunk_document_ir(ir)
    assert [chunk.chunk_index for chunk in first] == [chunk.chunk_index for chunk in second]
    assert [chunk.raw_content for chunk in first] == [chunk.raw_content for chunk in second]
    assert [chunk.retrieval_content for chunk in first] == [
        chunk.retrieval_content for chunk in second
    ]
    assert [chunk.content_hash for chunk in first] == [chunk.content_hash for chunk in second]
    assert [chunk.metadata for chunk in first] == [chunk.metadata for chunk in second]


@pytest.mark.parametrize(
    ("case_id", "row_label", "headers", "values"),
    [
        ("eval-001", "Ours (Lsimple)", ["IS", "FID"], ["9.46±0.11", "3.17"]),
        ("eval-002", "EEG2IM", ["Accuracy", "F1", "IS", "FID"], [".9", ".8", "8", "4"]),
        ("eval-022", "Lhybrid", ["FID"], ["3.19"]),
        ("eval-023", "H+L+FiLM", ["IS", "FID"], ["9.8", "3.1"]),
        ("eval-024", "T+F+KD", ["Accuracy", "F1"], [".91", ".90"]),
        ("eval-025", "LMM-Large", ["FID", "Accuracy"], [".12", ".96"]),
        ("eval-027", "Transformer", ["FIDtest", "Accuracy"], [".14", ".93"]),
        ("eval-028", "Action2Motion", ["FIDtr", "Accuracy"], [".21", ".88"]),
        ("eval-029", "Motion Intent", ["Accuracy", "Sensitivity"], [".94", ".92"]),
    ],
)
def test_nine_table_hard_case_contract_proxies_bind_headers_and_values(
    case_id: str, row_label: str, headers: list[str], values: list[str]
) -> None:
    cells = [_cell(0, 0, "Method", column_header=True, row_header=True)]
    cells.extend(_cell(0, index + 1, header, column_header=True) for index, header in enumerate(headers))
    cells.append(_cell(1, 0, row_label, row_header=True))
    cells.extend(_cell(1, index + 1, value) for index, value in enumerate(values))
    table = make_table_data(cells, row_count=2, column_count=len(headers) + 1, header_rows=[0])
    element = DocumentElement(
        kind="table",
        reading_order=0,
        raw_text=table.markdown,
        normalized_text=table.markdown,
        section_path=[case_id],
        provenance=[SourceSpan(physical_page=1)],
        table=table,
    )
    row = next(
        chunk
        for chunk in chunk_document_ir(make_ir(elements=[element]))
        if chunk.metadata["chunk_subtype"] == "table_row"
    )
    for header, value in zip(headers, values, strict=True):
        assert f"{header}: {value}" in row.retrieval_content


def test_eval_030_paragraph_relation_remains_in_one_chunk() -> None:
    text = "Method A requires 13.61 min, whereas Method B requires 13.09 min."
    element = DocumentElement(
        kind="paragraph",
        reading_order=0,
        raw_text=text,
        normalized_text=text,
        section_path=["Efficiency"],
        provenance=[SourceSpan(physical_page=3)],
    )
    chunks = chunk_document_ir(make_ir(elements=[element]))
    assert any("13.61 min" in chunk.raw_content and "13.09 min" in chunk.raw_content for chunk in chunks)


def test_long_row_groups_on_header_path_not_character_boundary() -> None:
    element = _multi_header_table()
    chunks = chunk_document_ir(make_ir(elements=[element]), ChunkConfig(max_chunk_chars=80))
    groups = [chunk for chunk in chunks if chunk.metadata["chunk_subtype"] == "table_group"]
    assert groups
    assert all(group.metadata["column_header_paths"] for group in groups)
    assert all(group.parent_chunk_index is not None for group in groups)

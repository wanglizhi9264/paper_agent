from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.schemas.chat import SourceOut
from app.services.retrieval import _citation_element_kind


def test_v2_table_source_exposes_physical_page_cells_and_bbox() -> None:
    source = SourceOut(
        index=1,
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title="EEG2IM",
        section_path=["Results"],
        page="7",
        page_start=7,
        page_end=7,
        element_id=uuid.uuid4(),
        element_kind="table",
        cell_ids=[uuid.uuid4()],
        bboxes=[{"physical_page": 7, "x0": 1, "y0": 2, "x1": 3, "y1": 4}],
        content="row",
        truncated=False,
    )
    assert source.bboxes[0].physical_page == 7
    assert source.element_kind == "table"


def test_v2_table_source_rejects_missing_provenance() -> None:
    with pytest.raises(ValidationError):
        SourceOut(
            index=1,
            chunk_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            document_title="paper",
            section_path=[],
            page="1",
            page_start=1,
            page_end=1,
            element_kind="table",
            content="row",
            truncated=False,
        )


def test_table_raw_text_source_accepts_bbox_level_provenance_without_cells() -> None:
    source = SourceOut(
        index=1,
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title="paper",
        section_path=["Results"],
        page="5",
        page_start=5,
        page_end=5,
        element_id=uuid.uuid4(),
        element_kind="table_raw_text",
        cell_ids=[],
        bboxes=[{"physical_page": 5, "x0": 1, "y0": 2, "x1": 3, "y1": 4}],
        content="raw table bbox text",
        truncated=False,
    )
    assert source.element_kind == "table_raw_text"
    assert source.cell_ids == []


def test_retrieval_marks_bbox_only_table_fallback_as_raw_text() -> None:
    assert (
        _citation_element_kind(
            {
                "element_kind": "table",
                "chunk_subtype": "table_raw_text",
                "cell_ids": [],
            }
        )
        == "table_raw_text"
    )
    assert (
        _citation_element_kind(
            {
                "element_kind": "table",
                "chunk_subtype": "table_row",
                "cell_ids": [str(uuid.uuid4())],
            }
        )
        == "table"
    )

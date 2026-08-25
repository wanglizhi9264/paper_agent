from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.schemas.chat import SourceOut


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

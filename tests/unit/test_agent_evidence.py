from __future__ import annotations

import uuid

from app.agent.evidence import merge_table_reads
from app.schemas.chat import BBoxOut
from tests.unit.test_paper_agent import evidence


def test_disjoint_table_reads_keep_both_numeric_results_and_provenance() -> None:
    first = evidence("row=8,column=2: 91.7 %")
    header, cell_a, cell_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    first.source = first.source.model_copy(
        update={
            "element_id": uuid.uuid4(),
            "element_kind": "table",
            "cell_ids": [header, cell_a],
            "bboxes": [BBoxOut(physical_page=1, x0=0, y0=0, x1=1, y1=1)],
        }
    )
    second = first.model_copy(deep=True)
    second.source.content = "row=12,column=2: 86.2 %"
    second.source.cell_ids = [header, cell_b]
    second.source.bboxes = [BBoxOut(physical_page=2, x0=0, y0=0, x1=1, y1=1)]
    second.source.page_start = second.source.page_end = 2
    merged = merge_table_reads(first, second)
    assert merged.source.content == first.source.content + "\n\n" + second.source.content
    assert merged.source.cell_ids == [header, cell_a, cell_b]
    assert len(merged.source.bboxes) == 2
    assert merged.source.page_start == 1
    assert merged.source.page_end == 2
    assert merged.source.chunk_id == first.source.chunk_id
    assert merge_table_reads(merged, first) == merged
    assert merge_table_reads(first, merged) == merged
    assert first.source.cell_ids == [header, cell_a]


def test_table_merge_never_combines_different_versions_or_chunks() -> None:
    first = evidence("old")
    second = first.model_copy(deep=True)
    second.document_version_id = uuid.uuid4()
    assert merge_table_reads(first, second) == second
    second = evidence("other paper")
    assert merge_table_reads(first, second) == second

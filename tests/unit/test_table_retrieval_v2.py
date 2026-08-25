from __future__ import annotations

import uuid

from app.retrieval.table import TableContextChunk, expand_table_context


def _chunk(
    index: int,
    content: str,
    *,
    subtype: str,
    parent_id: uuid.UUID | None = None,
    headers: list[list[str]] | None = None,
    content_hash: str | None = None,
) -> TableContextChunk:
    return TableContextChunk(
        chunk_id=uuid.uuid4(),
        chunk_index=index,
        raw_content=content,
        retrieval_content=content,
        content_hash=content_hash or f"hash-{index}",
        parent_chunk_id=parent_id,
        subtype=subtype,
        column_header_paths=headers or [],
    )


def test_table_hit_keeps_row_as_marker_and_packs_parent_first() -> None:
    parent = _chunk(0, "Table EEG2IM headers", subtype="table_parent")
    row = _chunk(
        2,
        "H+L+FiLM | IS 9.8 | FID 12.1",
        subtype="table_row",
        parent_id=parent.chunk_id,
        headers=[["ImageNet-4", "IS"], ["ImageNet-4", "FID"]],
    )

    expanded = expand_table_context(row, [parent, row], "ImageNet-4 FiLM IS FID")

    assert expanded.marker_chunk_id == row.chunk_id
    assert expanded.chunk_ids == [parent.chunk_id, row.chunk_id]
    assert expanded.content.index(parent.raw_content) < expanded.content.index(row.raw_content)


def test_table_expansion_selects_at_most_two_query_relevant_adjacent_rows() -> None:
    parent = _chunk(0, "headers", subtype="table_parent")
    rows = [
        _chunk(
            index,
            content,
            subtype="table_row",
            parent_id=parent.chunk_id,
            headers=[[dataset, metric]],
        )
        for index, content, dataset, metric in [
            (1, "baseline CIFAR accuracy", "CIFAR", "accuracy"),
            (2, "H+L ImageNet-4 IS", "ImageNet-4", "IS"),
            (3, "H+L+FiLM ImageNet-4 IS FID", "ImageNet-4", "FID"),
            (4, "ablation ImageNet-4 FID", "ImageNet-4", "FID"),
            (5, "unrelated latency", "COCO", "latency"),
        ]
    ]

    expanded = expand_table_context(
        rows[2], [parent, *rows], "ImageNet-4 H+L+FiLM IS FID", max_adjacent_rows=2
    )

    assert expanded.marker_chunk_id == rows[2].chunk_id
    assert len(expanded.chunk_ids) == 4
    assert parent.chunk_id in expanded.chunk_ids
    assert rows[2].chunk_id in expanded.chunk_ids
    assert rows[4].chunk_id not in expanded.chunk_ids


def test_expansion_deduplicates_by_id_then_content_hash() -> None:
    parent = _chunk(0, "headers", subtype="table_parent")
    hit = _chunk(1, "metric value", subtype="table_row", parent_id=parent.chunk_id)
    duplicate = _chunk(
        2,
        "metric value",
        subtype="table_row",
        parent_id=parent.chunk_id,
        content_hash=hit.content_hash,
    )

    expanded = expand_table_context(hit, [parent, hit, hit, duplicate], "metric")

    assert expanded.chunk_ids == [parent.chunk_id, hit.chunk_id]


def test_non_table_hit_is_not_expanded() -> None:
    paragraph = _chunk(1, "plain paragraph", subtype="")
    expanded = expand_table_context(paragraph, [paragraph], "plain")
    assert expanded.marker_chunk_id == paragraph.chunk_id
    assert expanded.chunk_ids == [paragraph.chunk_id]
    assert expanded.content == paragraph.raw_content

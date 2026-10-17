from __future__ import annotations

from app.agent.contracts import Evidence


def merge_table_reads(previous: Evidence, current: Evidence) -> Evidence:
    """Union verified cells for one citation identity, preserving earlier evidence first."""
    old, new = previous.source, current.source
    if (
        old.chunk_id != new.chunk_id
        or old.document_id != new.document_id
        or previous.document_version_id != current.document_version_id
        or previous.content_hash != current.content_hash
        or old.element_kind != "table"
        or new.element_kind != "table"
        or old.element_id != new.element_id
    ):
        return current
    if set(old.cell_ids).issubset(new.cell_ids):
        return current
    if set(new.cell_ids).issubset(old.cell_ids):
        return previous
    boxes = {box.model_dump_json(): box for box in [*old.bboxes, *new.bboxes]}
    starts = [p for p in [old.page_start, new.page_start] if p is not None]
    ends = [p for p in [old.page_end, new.page_end] if p is not None]
    source = new.model_copy(
        update={
            "content": old.content + "\n\n" + new.content,
            "cell_ids": list(dict.fromkeys([*old.cell_ids, *new.cell_ids])),
            "bboxes": list(boxes.values()),
            "page_start": min(starts) if starts else None,
            "page_end": max(ends) if ends else None,
            "truncated": old.truncated or new.truncated,
        }
    )
    return current.model_copy(update={"source": source})

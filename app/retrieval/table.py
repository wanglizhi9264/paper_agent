"""Bounded, query-aware context expansion for table retrieval hits."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TableContextChunk:
    chunk_id: uuid.UUID
    chunk_index: int
    raw_content: str
    retrieval_content: str
    content_hash: str
    parent_chunk_id: uuid.UUID | None
    subtype: str
    column_header_paths: list[list[str]] = field(default_factory=list)


@dataclass(frozen=True)
class ExpandedTableContext:
    marker_chunk_id: uuid.UUID
    content: str
    chunk_ids: list[uuid.UUID]


def _terms(value: str) -> set[str]:
    return {term.casefold() for term in re.findall(r"[\w.+-]+", value, re.UNICODE)}


def _overlap(candidate: TableContextChunk, query: str) -> int:
    header_text = " ".join(part for path in candidate.column_header_paths for part in path)
    return len(_terms(query) & _terms(f"{header_text} {candidate.retrieval_content}"))


def _candidate_score(
    hit: TableContextChunk, candidate: TableContextChunk, query: str
) -> tuple[int, int, int]:
    overlap = _overlap(candidate, query)
    distance = abs(candidate.chunk_index - hit.chunk_index)
    return (-overlap, distance, candidate.chunk_index)


def _deduplicate(chunks: list[TableContextChunk]) -> list[TableContextChunk]:
    ids: set[uuid.UUID] = set()
    hashes: set[str] = set()
    result: list[TableContextChunk] = []
    for chunk in chunks:
        if chunk.chunk_id in ids or chunk.content_hash in hashes:
            continue
        ids.add(chunk.chunk_id)
        hashes.add(chunk.content_hash)
        result.append(chunk)
    return result


def expand_table_context(
    hit: TableContextChunk,
    available_chunks: list[TableContextChunk],
    query: str,
    *,
    max_adjacent_rows: int = 2,
) -> ExpandedTableContext:
    """Pack parent, hit, and at most two related rows while citing the hit.

    Expansion happens after ranking. It never assigns a score/source marker to
    a parent or neighbor and never expands a non-table row/group hit.
    """
    if hit.subtype not in {"table_row", "table_group"} or hit.parent_chunk_id is None:
        return ExpandedTableContext(hit.chunk_id, hit.raw_content, [hit.chunk_id])

    by_id = {chunk.chunk_id: chunk for chunk in available_chunks}
    parent = by_id.get(hit.parent_chunk_id)
    candidates = [
        chunk
        for chunk in available_chunks
        if chunk.chunk_id != hit.chunk_id
        and chunk.parent_chunk_id == hit.parent_chunk_id
        and chunk.subtype in {"table_row", "table_group"}
        and _overlap(chunk, query) > 0
    ]
    candidates.sort(key=lambda chunk: _candidate_score(hit, chunk, query))
    selected = candidates[: max(0, min(max_adjacent_rows, 2))]
    ordered = ([parent] if parent is not None else []) + [hit, *selected]
    unique = _deduplicate(ordered)
    return ExpandedTableContext(
        marker_chunk_id=hit.chunk_id,
        content="\n\n".join(chunk.raw_content for chunk in unique),
        chunk_ids=[chunk.chunk_id for chunk in unique],
    )

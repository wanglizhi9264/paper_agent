from __future__ import annotations

from app.retrieval.fusion import RetrievalResult


def dedup_by_chunk_id(results: list[RetrievalResult]) -> list[RetrievalResult]:
    """Remove duplicate chunk_ids, keeping first occurrence (spec §14.4)."""
    seen: set[str] = set()
    out: list[RetrievalResult] = []
    for r in results:
        if r.chunk_id not in seen:
            seen.add(r.chunk_id)
            out.append(r)
    return out


def dedup_by_content_hash(results: list[RetrievalResult]) -> list[RetrievalResult]:
    """After chunk_id dedup, remove duplicate content_hash (spec §14.4)."""
    seen: set[str] = set()
    out: list[RetrievalResult] = []
    for r in results:
        h = r.metadata.get("content_hash", "") if r.metadata else ""
        if not h:
            h = r.raw_content  # fallback
        if h not in seen:
            seen.add(h)
            out.append(r)
    return out


def dedup(results: list[RetrievalResult]) -> list[RetrievalResult]:
    """Full dedup: first by chunk_id, then by content_hash (spec §14.4)."""
    return dedup_by_content_hash(dedup_by_chunk_id(results))


def neighbor_expansion(
    results: list[RetrievalResult],
    all_chunks: dict[str, list[RetrievalResult]],
    *,
    window: int = 1,
) -> list[RetrievalResult]:
    """Expand neighbors: for each result, add adjacent chunks in the same
    document and section (spec §14.4).

    Expanded chunks don't get a new retrieval score; they carry
    ``expanded_from_chunk_id``.

    ``all_chunks`` maps document_id -> ordered list of chunks.
    """
    expanded: list[RetrievalResult] = []
    existing_ids = {r.chunk_id for r in results}

    for r in results:
        expanded.append(r)
        doc_chunks = all_chunks.get(r.document_id, [])
        # Find the index of the current chunk.
        idx = -1
        for i, c in enumerate(doc_chunks):
            if c.chunk_id == r.chunk_id:
                idx = i
                break
        if idx < 0:
            continue
        # Add neighbors.
        for offset in range(-window, window + 1):
            if offset == 0:
                continue
            ni = idx + offset
            if ni < 0 or ni >= len(doc_chunks):
                continue
            neighbor = doc_chunks[ni]
            # Same section path check.
            if neighbor.section_path != r.section_path:
                continue
            if neighbor.chunk_id in existing_ids:
                continue
            existing_ids.add(neighbor.chunk_id)
            expanded.append(
                RetrievalResult(
                    chunk_id=neighbor.chunk_id,
                    faiss_id=neighbor.faiss_id,
                    score=0.0,
                    source="expanded",
                    rank=r.rank,
                    document_id=neighbor.document_id,
                    section_path=neighbor.section_path,
                    raw_content=neighbor.raw_content,
                    retrieval_content=neighbor.retrieval_content,
                    page_start=neighbor.page_start,
                    page_end=neighbor.page_end,
                    metadata=neighbor.metadata,
                    expanded_from_chunk_id=r.chunk_id,
                )
            )
    return expanded

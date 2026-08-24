from __future__ import annotations

from dataclasses import dataclass

from app.retrieval.fusion import RetrievalResult


@dataclass
class SourceBlock:
    """A packed source block in context (spec §14.5)."""

    index: int  # 1-based [Source N]
    chunk_id: str
    document_title: str
    section_path: list[str]
    page: str
    content: str
    truncated: bool = False


def pack_context(
    results: list[RetrievalResult],
    *,
    document_titles: dict[str, str] | None = None,
    budget_tokens: int = 4292,  # 8192 - 1200 system - 1200 history - 1500 answer
    tokens_per_char: float = 0.25,  # approx 4 chars per token
    merge_adjacent: bool = True,
) -> list[SourceBlock]:
    """Pack retrieval results into source blocks (spec §14.5).

    - First by rerank order.
    - Merge adjacent chunks in same section.
    - Truncate by sentence boundary when too long.
    - Assign [Source N] markers.
    """
    titles = document_titles or {}
    blocks: list[SourceBlock] = []
    used_tokens = 0
    budget_chars = int(budget_tokens / tokens_per_char)

    # Group by document+section for merging.
    i = 0
    while i < len(results):
        r = results[i]
        title = titles.get(r.document_id, "Unknown")
        section = r.section_path or []
        page = str(r.page_start) if r.page_start else "unknown"
        content = r.raw_content

        # Try to merge with adjacent chunks in same section.
        if merge_adjacent:
            j = i + 1
            while j < len(results):
                next_r = results[j]
                if (
                    next_r.document_id == r.document_id
                    and (next_r.section_path or []) == section
                    and next_r.expanded_from_chunk_id is not None
                ):
                    content += "\n" + next_r.raw_content
                    j += 1
                else:
                    break
            i = j
        else:
            i += 1

        # Truncate if too long, at sentence boundary.
        truncated = False
        if len(content) + used_tokens > budget_chars:
            remaining = budget_chars - used_tokens
            if remaining <= 0:
                break
            if len(content) > remaining:
                # Try sentence boundary.
                cut = _find_sentence_boundary(content, remaining)
                content = content[:cut].rstrip()
                truncated = True

        used_tokens += len(content)
        blocks.append(
            SourceBlock(
                index=len(blocks) + 1,
                chunk_id=r.chunk_id,
                document_title=title,
                section_path=section,
                page=page,
                content=content,
                truncated=truncated,
            )
        )

    return blocks


def _find_sentence_boundary(text: str, max_chars: int) -> int:
    """Find the last sentence boundary before max_chars."""
    for i in range(min(max_chars, len(text)) - 1, 0, -1):
        if text[i] in ".!?。！？":
            return i + 1
    return max_chars


def format_source_block(block: SourceBlock) -> str:
    """Format a source block for the LLM prompt (spec §14.5)."""
    section_str = " > ".join(block.section_path) if block.section_path else "Unknown"
    truncated_note = " [truncated]" if block.truncated else ""
    return (
        f"[Source {block.index}]\n"
        f"Document: {block.document_title}\n"
        f"Section: {section_str}\n"
        f"Page: {block.page}\n"
        f"Chunk-ID: {block.chunk_id}\n"
        f"Content:\n{block.content}{truncated_note}"
    )


def build_citation_map(blocks: list[SourceBlock]) -> dict[int, str]:
    """Map citation index -> chunk_id (spec §15)."""
    return {b.index: b.chunk_id for b in blocks}

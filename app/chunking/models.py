"""Chunking data models and configuration (spec §12)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ChunkKindStr = Literal["text", "title", "table", "code", "chapter"]


@dataclass(frozen=True, slots=True)
class ChunkConfig:
    """Configuration for the chunking pipeline.

    All values come from spec §12.1. Changing any field that affects chunk
    boundaries requires a reindex (new DocumentVersion).
    """

    small_document_not_chunk: bool = True
    small_document_char_threshold: int = 2048
    max_chunk_chars: int = 800
    sentence_merge_num: int = 12
    sentence_on: bool = True
    table_on: bool = True
    title_chunk_on: bool = True
    need_chapter: bool = False
    code_not_add_index: bool = False
    retrieval_content_max_chars: int = 30000
    md_heading_max_level: int = 10
    neighbor_window: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "small_document_not_chunk": self.small_document_not_chunk,
            "small_document_char_threshold": self.small_document_char_threshold,
            "max_chunk_chars": self.max_chunk_chars,
            "sentence_merge_num": self.sentence_merge_num,
            "sentence_on": self.sentence_on,
            "table_on": self.table_on,
            "title_chunk_on": self.title_chunk_on,
            "need_chapter": self.need_chapter,
            "code_not_add_index": self.code_not_add_index,
            "retrieval_content_max_chars": self.retrieval_content_max_chars,
            "md_heading_max_level": self.md_heading_max_level,
            "neighbor_window": self.neighbor_window,
        }

    @staticmethod
    def default() -> ChunkConfig:
        return ChunkConfig()


@dataclass(slots=True)
class ChunkResult:
    """Output of the chunking pipeline, before persistence as ORM Chunk rows.

    ``chunk_index`` is assigned in document order (0-based). ``content_hash``
    is SHA-256 of normalized ``raw_content``. ``retrieval_content`` is the
    deterministic concatenation of title + section_path + raw_content
    (spec §12.2 rule 5), truncated to ``retrieval_content_max_chars``.
    """

    chunk_index: int
    kind: ChunkKindStr
    section_path: list[str]
    raw_content: str
    retrieval_content: str
    content_hash: str
    character_count: int
    page_start: int | None = None
    page_end: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    parent_chunk_index: int | None = None
    chapter_chunk_index: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def add_to_index(self) -> bool:
        """Whether this chunk enters the dense/BM25 index.

        Code chunks with ``code_not_add_index=True`` are stored but excluded.
        """
        if self.kind == "code":
            return not self.metadata.get("code_not_add_index", False)
        if self.kind == "table" and self.metadata.get("chunk_subtype") == "table_parent":
            return False
        return True


DEFAULT_CHUNK_CONFIG = ChunkConfig.default()

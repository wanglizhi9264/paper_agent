"""Deterministic chunking pipeline (spec §12).

Pipeline:
    ParsedDocument
    -> Heading tree
    -> Parent merge (adjacent same-section paragraphs)
    -> Fine split (sentence merge / over-long split)
    -> Optional title/table/chapter chunks
    -> Retrieval content prefix + hash

The pipeline is pure: given the same ``ParsedDocument`` and ``ChunkConfig``,
it always produces the same ``ChunkResult`` list with identical content and
hashes. No DB, no I/O.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from app.chunking.heading_tree import (
    HeadingNode,
    build_heading_tree,
    collect_content_in_order,
)
from app.chunking.models import ChunkConfig, ChunkResult
from app.chunking.sentence import Sentence, split_sentences
from app.loaders.base import Paragraph, ParsedDocument

if TYPE_CHECKING:
    from app.document_ir.models import DocumentIR


def chunk_document(
    document: ParsedDocument, config: ChunkConfig | None = None
) -> list[ChunkResult]:
    """Chunk a parsed document into ``ChunkResult`` objects.

    Deterministic: same input + config -> same output (chunk_index, content,
    content_hash, retrieval_content). No side effects.
    """
    cfg = config or ChunkConfig.default()
    title = document.title
    total_chars = sum(len(p.content) for p in document.paragraphs)

    # Small document shortcut (spec §12.2 rule 1).
    if cfg.small_document_not_chunk and total_chars <= cfg.small_document_char_threshold:
        return _small_document_chunks(document, cfg, title, total_chars)

    # Build heading tree.
    tree = build_heading_tree(document.paragraphs, max_level=cfg.md_heading_max_level)

    # Optional title chunks.
    results: list[ChunkResult] = []
    title_indices: dict[str, int] = {}  # section_path key -> chunk_index
    if cfg.title_chunk_on:
        for node in tree.all_nodes:
            if node.title and node.paragraphs:
                idx = len(results)
                cr = _make_title_chunk(idx, node, title, cfg)
                results.append(cr)
                title_indices[_path_key(node.section_path)] = idx

    # Content chunks.
    ordered = collect_content_in_order(tree)
    _chunk_content(ordered, results, title, cfg, title_indices)

    # Re-index to ensure contiguous 0..N-1 (title chunks were inserted first,
    # but content chunks appended after; order is title-first then content).
    # Already contiguous since we append in order.
    return results


def _small_document_chunks(
    document: ParsedDocument,
    cfg: ChunkConfig,
    title: str,
    total_chars: int,
) -> list[ChunkResult]:
    """Single text chunk for small documents (spec §12.2 rule 1)."""
    raw_content = "\n\n".join(p.content for p in document.paragraphs)
    section_path: list[str] = []
    # If there's a heading, use it for section_path.
    for p in document.paragraphs:
        level = p.metadata.get("heading_level")
        if level is not None and isinstance(level, int) and level > 0:
            section_path = [p.metadata.get("heading", p.content)]
            break

    page_start = min((p.page for p in document.paragraphs if p.page), default=None)
    page_end = max((p.page for p in document.paragraphs if p.page), default=None)
    line_start = min((p.line_start for p in document.paragraphs if p.line_start), default=None)
    line_end = max((p.line_end for p in document.paragraphs if p.line_end), default=None)

    retrieval = _build_retrieval_content(title, section_path, raw_content, cfg)
    return [
        ChunkResult(
            chunk_index=0,
            kind="text",
            section_path=section_path,
            raw_content=raw_content,
            retrieval_content=retrieval,
            content_hash=_hash(raw_content),
            character_count=len(raw_content),
            page_start=page_start,
            page_end=page_end,
            line_start=line_start,
            line_end=line_end,
            metadata={},
        )
    ]


def _chunk_content(
    ordered: list[tuple[HeadingNode, Paragraph]],
    results: list[ChunkResult],
    title: str,
    cfg: ChunkConfig,
    title_indices: dict[str, int],
) -> None:
    """Process content paragraphs into text/table/code chunks."""
    # Group consecutive paragraphs by node (same section) for parent merge.
    groups: list[list[tuple[HeadingNode, Paragraph]]] = []
    current_group: list[tuple[HeadingNode, Paragraph]] = []
    current_node_id: int | None = None

    for node, para in ordered:
        node_id = id(node)
        if current_node_id is None or node_id == current_node_id:
            current_group.append((node, para))
            current_node_id = node_id
        else:
            if current_group:
                groups.append(current_group)
            current_group = [(node, para)]
            current_node_id = node_id
    if current_group:
        groups.append(current_group)

    for group in groups:
        if not group:
            continue
        node = group[0][0]
        section_path = node.section_path
        title_idx = title_indices.get(_path_key(section_path))

        # Tables and code stay standalone. Consecutive text blocks from PDF
        # extraction are merged before sentence splitting; otherwise a PDF
        # loader that emits one line/span per Paragraph creates unusably tiny
        # chunks and evidence phrases can be split across chunk boundaries.
        text_run: list[Paragraph] = []

        for _pnode, para in group:
            if para.type == "table":
                _flush_text_run(results, text_run, section_path, title, cfg, title_idx)
                _add_table_chunk(results, para, section_path, title, cfg, title_idx)
            elif para.type == "code":
                _flush_text_run(results, text_run, section_path, title, cfg, title_idx)
                _add_code_chunk(results, para, section_path, title, cfg, title_idx)
            else:
                if text_run and para.page != text_run[-1].page:
                    _flush_text_run(results, text_run, section_path, title, cfg, title_idx)
                text_run.append(para)
        _flush_text_run(results, text_run, section_path, title, cfg, title_idx)


def _flush_text_run(
    results: list[ChunkResult],
    text_run: list[Paragraph],
    section_path: list[str],
    title: str,
    cfg: ChunkConfig,
    title_idx: int | None,
) -> None:
    if not text_run:
        return
    first, last = text_run[0], text_run[-1]
    merged = Paragraph(
        type="text",
        content="\n".join(p.content for p in text_run if p.content.strip()),
        page=first.page,
        line_start=first.line_start,
        line_end=last.line_end,
        metadata={"page_end": last.page},
    )
    _add_text_chunks(results, merged, section_path, title, cfg, title_idx)
    text_run.clear()


def _add_text_chunks(
    results: list[ChunkResult],
    para: Paragraph,
    section_path: list[str],
    title: str,
    cfg: ChunkConfig,
    title_idx: int | None,
) -> None:
    """Split a text paragraph into sentence-merged chunks."""
    if cfg.sentence_on:
        sentences = split_sentences(para.content, max_chunk_chars=cfg.max_chunk_chars)
    # Paragraph as basic unit (spec §12.2 rule 6).
    elif len(para.content) <= cfg.max_chunk_chars:
        sentences = [Sentence(text=para.content)]
    else:
        sentences = split_sentences(para.content, max_chunk_chars=cfg.max_chunk_chars)

    # Merge sentences: up to sentence_merge_num sentences, <= max_chunk_chars.
    merged: list[tuple[str, bool]] = []  # (text, hard_split)
    buf = ""
    buf_count = 0
    buf_hard = False
    for s in sentences:
        if buf_count >= cfg.sentence_merge_num or len(buf) + len(s.text) > cfg.max_chunk_chars:
            if buf:
                merged.append((buf, buf_hard))
            buf = s.text
            buf_count = 1
            buf_hard = s.hard_split
        else:
            if buf:
                buf += " " + s.text
            else:
                buf = s.text
            buf_count += 1
            buf_hard = buf_hard or s.hard_split
    if buf:
        merged.append((buf, buf_hard))

    for raw_text, hard in merged:
        chunk_text = raw_text.strip()
        if not chunk_text:
            continue
        idx = len(results)
        meta: dict[str, Any] = {}
        if hard:
            meta["hard_split"] = True
        retrieval = _build_retrieval_content(title, section_path, chunk_text, cfg)
        results.append(
            ChunkResult(
                chunk_index=idx,
                kind="text",
                section_path=list(section_path),
                raw_content=chunk_text,
                retrieval_content=retrieval,
                content_hash=_hash(chunk_text),
                character_count=len(chunk_text),
                page_start=para.page,
                page_end=para.metadata.get("page_end", para.page),
                line_start=para.line_start,
                line_end=para.line_end,
                parent_chunk_index=title_idx,
                metadata=meta,
            )
        )


def _add_table_chunk(
    results: list[ChunkResult],
    para: Paragraph,
    section_path: list[str],
    title: str,
    cfg: ChunkConfig,
    title_idx: int | None,
) -> None:
    """Table chunks: if over max_chunk_chars, split repeating the header row."""
    if not cfg.table_on:
        # Serialize as plain text (spec §12.2 rule 6).
        _add_text_chunks(results, para, section_path, title, cfg, title_idx)
        return

    lines = para.content.split("\n")
    if len(lines) < 2:
        _add_text_chunks(results, para, section_path, title, cfg, title_idx)
        return

    # First line is header; second is separator (---). Rest are data rows.
    header = lines[0]
    separator = lines[1] if len(lines) > 1 else ""
    data_rows = lines[2:]

    # If table fits in one chunk, keep as-is.
    if len(para.content) <= cfg.max_chunk_chars:
        idx = len(results)
        retrieval = _build_retrieval_content(title, section_path, para.content, cfg)
        results.append(
            ChunkResult(
                chunk_index=idx,
                kind="table",
                section_path=list(section_path),
                raw_content=para.content,
                retrieval_content=retrieval,
                content_hash=_hash(para.content),
                character_count=len(para.content),
                page_start=para.page,
                page_end=para.page,
                line_start=para.line_start,
                line_end=para.line_end,
                parent_chunk_index=title_idx,
                metadata=para.metadata,
            )
        )
        return

    # Split: each fragment repeats header + separator.
    current_lines = [header, separator]
    current_len = len(header) + len(separator) + 2
    for row in data_rows:
        if current_len + len(row) + 1 > cfg.max_chunk_chars and len(current_lines) > 2:
            _emit_table_fragment(results, current_lines, section_path, title, cfg, para, title_idx)
            current_lines = [header, separator]
            current_len = len(header) + len(separator) + 2
        current_lines.append(row)
        current_len += len(row) + 1
    if len(current_lines) > 2:
        _emit_table_fragment(results, current_lines, section_path, title, cfg, para, title_idx)


def _emit_table_fragment(
    results: list[ChunkResult],
    lines: list[str],
    section_path: list[str],
    title: str,
    cfg: ChunkConfig,
    para: Paragraph,
    title_idx: int | None,
) -> None:
    content = "\n".join(lines)
    idx = len(results)
    retrieval = _build_retrieval_content(title, section_path, content, cfg)
    results.append(
        ChunkResult(
            chunk_index=idx,
            kind="table",
            section_path=list(section_path),
            raw_content=content,
            retrieval_content=retrieval,
            content_hash=_hash(content),
            character_count=len(content),
            page_start=para.page,
            page_end=para.page,
            line_start=para.line_start,
            line_end=para.line_end,
            parent_chunk_index=title_idx,
            metadata={**para.metadata, "fragment": True},
        )
    )


def _add_code_chunk(
    results: list[ChunkResult],
    para: Paragraph,
    section_path: list[str],
    title: str,
    cfg: ChunkConfig,
    title_idx: int | None,
) -> None:
    """Code chunks: stored as-is (not sentence-split). Marked with code_not_add_index."""
    idx = len(results)
    retrieval = _build_retrieval_content(title, section_path, para.content, cfg)
    meta: dict[str, Any] = {**para.metadata}
    if cfg.code_not_add_index:
        meta["code_not_add_index"] = True
    results.append(
        ChunkResult(
            chunk_index=idx,
            kind="code",
            section_path=list(section_path),
            raw_content=para.content,
            retrieval_content=retrieval,
            content_hash=_hash(para.content),
            character_count=len(para.content),
            page_start=para.page,
            page_end=para.page,
            line_start=para.line_start,
            line_end=para.line_end,
            parent_chunk_index=title_idx,
            metadata=meta,
        )
    )


def _make_title_chunk(idx: int, node: HeadingNode, doc_title: str, cfg: ChunkConfig) -> ChunkResult:
    """A title chunk containing just the heading text."""
    text = node.title
    retrieval = _build_retrieval_content(doc_title, node.section_path, text, cfg)
    return ChunkResult(
        chunk_index=idx,
        kind="title",
        section_path=list(node.section_path),
        raw_content=text,
        retrieval_content=retrieval,
        content_hash=_hash(text),
        character_count=len(text),
        metadata={"heading_level": node.level},
    )


def _build_retrieval_content(
    title: str, section_path: list[str], raw_content: str, cfg: ChunkConfig
) -> str:
    """Deterministic concatenation: title + section_path + raw_content (spec §12.2 rule 5).

    Truncated to ``retrieval_content_max_chars``. ``raw_content`` gets no
    retrieval prefix.
    """
    parts: list[str] = []
    if title:
        parts.append(title)
    if section_path:
        parts.append(" > ".join(section_path))
    parts.append(raw_content)
    text = "\n".join(parts)
    if len(text) > cfg.retrieval_content_max_chars:
        text = text[: cfg.retrieval_content_max_chars]
    return text


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _path_key(section_path: list[str]) -> str:
    return "\x00".join(section_path)


def chunk_document_ir(
    document: DocumentIR, config: ChunkConfig | None = None
) -> list[ChunkResult]:
    """Chunk Canonical Document IR without branching on parser identity."""
    from app.chunking.table import chunk_table_element
    from app.document_ir.normalize import formula_search_aliases

    cfg = config or ChunkConfig.default()
    results: list[ChunkResult] = []
    for element in sorted(document.elements, key=lambda value: value.reading_order):
        if element.kind in {"header", "footer", "figure"}:
            continue
        if element.kind == "table":
            if not cfg.table_on:
                para = Paragraph(
                    type="text",
                    content=element.raw_text,
                    page=element.provenance[0].physical_page,
                    metadata={"element_id": str(element.id), "element_kind": "table"},
                )
                _add_text_chunks(
                    results, para, element.section_path, document.title, cfg, None
                )
            else:
                results.extend(
                    chunk_table_element(
                        element,
                        document_title=document.title,
                        start_index=len(results),
                        config=cfg,
                    )
                )
            continue

        if element.kind in {"title", "heading"} and not cfg.title_chunk_on:
            continue
        page_numbers = [span.physical_page for span in element.provenance]
        metadata: dict[str, Any] = {
            "ir_schema_version": 2,
            "element_id": str(element.id),
            "element_kind": element.kind,
            "physical_pages": sorted(set(page_numbers)),
            "bboxes": [
                {
                    "physical_page": span.physical_page,
                    "x0": span.bbox.x0,
                    "y0": span.bbox.y0,
                    "x1": span.bbox.x1,
                    "y1": span.bbox.y1,
                }
                for span in element.provenance
                if span.bbox is not None
            ],
            "cell_ids": [],
        }
        aliases = formula_search_aliases(element.normalized_text) if element.kind == "formula" else []
        if aliases:
            metadata["search_aliases"] = aliases
        para_type = "code" if element.kind == "code" else "text"
        before = len(results)
        para = Paragraph(
            type=para_type,
            content=element.raw_text,
            page=min(page_numbers, default=None),
            metadata={"page_end": max(page_numbers, default=None), **metadata},
        )
        if para_type == "code":
            _add_code_chunk(results, para, element.section_path, document.title, cfg, None)
        else:
            _add_text_chunks(results, para, element.section_path, document.title, cfg, None)
        for chunk in results[before:]:
            chunk.metadata.update(metadata)
            normalized = element.normalized_text
            if aliases:
                normalized = f"{normalized} {' '.join(aliases)}"
            chunk.retrieval_content = _build_retrieval_content(
                document.title, element.section_path, normalized, cfg
            )
            if element.kind in {"title", "heading"}:
                chunk.kind = "title"
    return results

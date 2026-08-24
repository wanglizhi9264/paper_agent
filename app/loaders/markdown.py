"""Markdown loader using markdown-it-py.

Parses ATX/Setext headings, paragraphs, fenced code blocks, and pipe tables.
Code language is captured in metadata. HTML and scripts are never executed
(spec §11.3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.loaders.base import (
    BaseLoader,
    LoaderError,
    Paragraph,
    ParsedDocument,
    normalize_text,
)


class MarkdownLoader:
    """markdown-it-py-based loader for .md files."""

    supported_extensions = frozenset({"md"})

    def load(self, path: Path) -> ParsedDocument:
        try:
            from markdown_it import MarkdownIt
        except ImportError as exc:
            raise LoaderError("markdown-it-py is not installed", code="DEPENDENCY_MISSING") from exc

        if not path.exists():
            raise LoaderError(f"File not found: {path.name}", code="FILE_NOT_FOUND")

        raw = path.read_text(encoding="utf-8", errors="replace")
        text = normalize_text(raw)
        md = MarkdownIt("commonmark", {"html": False}).enable("table")
        tokens = md.parse(text)

        paragraphs: list[Paragraph] = []
        current_line = 1
        title = ""
        i = 0

        while i < len(tokens):
            tok = tokens[i]
            if tok.type == "heading_open":
                level = int(tok.tag[1:]) if tok.tag.startswith("h") else 0
                inline_tok = tokens[i + 1] if i + 1 < len(tokens) else None
                heading_text = _inline_content(inline_tok) if inline_tok else ""
                heading_text = normalize_text(heading_text).strip()
                if heading_text:
                    line_count = heading_text.count("\n") + 1
                    paragraphs.append(
                        Paragraph(
                            type="markdown",
                            content=heading_text,
                            page=None,
                            line_start=current_line,
                            line_end=current_line + line_count - 1,
                            metadata={"heading_level": level, "heading": heading_text},
                        )
                    )
                    if not title and level == 1:
                        title = heading_text
                    current_line += line_count + 1
                i += 3  # heading_open, inline, heading_close
                continue
            if tok.type == "fence":
                code = normalize_text(tok.content)
                lang = tok.info.strip() if tok.info else ""
                if code:
                    line_count = code.count("\n") + 1
                    paragraphs.append(
                        Paragraph(
                            type="code",
                            content=code,
                            page=None,
                            line_start=current_line,
                            line_end=current_line + line_count - 1,
                            metadata={"language": lang} if lang else {},
                        )
                    )
                    current_line += line_count + 1
                i += 1
                continue
            if tok.type == "paragraph_open":
                inline_tok = tokens[i + 1] if i + 1 < len(tokens) else None
                para_text = _inline_content(inline_tok) if inline_tok else ""
                para_text = normalize_text(para_text).strip()
                if para_text:
                    line_count = para_text.count("\n") + 1
                    paragraphs.append(
                        Paragraph(
                            type="markdown",
                            content=para_text,
                            page=None,
                            line_start=current_line,
                            line_end=current_line + line_count - 1,
                            metadata={},
                        )
                    )
                    if not title:
                        title = para_text.split("\n", 1)[0][:300]
                    current_line += line_count + 1
                i += 3  # paragraph_open, inline, paragraph_close
                continue
            if tok.type == "table_open":
                table_text, consumed = _extract_table(tokens, i)
                table_text = normalize_text(table_text).strip()
                if table_text:
                    line_count = table_text.count("\n") + 1
                    paragraphs.append(
                        Paragraph(
                            type="table",
                            content=table_text,
                            page=None,
                            line_start=current_line,
                            line_end=current_line + line_count - 1,
                            metadata={},
                        )
                    )
                    current_line += line_count + 1
                i += consumed
                continue
            # Skip tokens we don't explicitly handle (hr, list markers, etc.)
            i += 1

        if not title and paragraphs:
            title = paragraphs[0].content.split("\n", 1)[0][:300]

        total_chars = sum(len(p.content) for p in paragraphs)
        metadata = {
            "page_count": None,
            "character_count": total_chars,
            "loader": "markdown-it-py",
            "paragraph_count": len(paragraphs),
        }
        return ParsedDocument(title=title, paragraphs=paragraphs, metadata=metadata)


def _inline_content(tok: Any) -> str:
    """Extract plain text from an inline token."""
    if tok is None or tok.children is None:
        return tok.content if tok is not None else ""
    parts = []
    for child in tok.children:
        if child.type == "text":
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append("\n")
        elif child.type == "code_inline":
            parts.append(child.content)
        elif child.type == "image":
            alt = child.content
            parts.append(alt)
        elif child.type in ("link_open", "link_close"):
            continue
        else:
            parts.append(child.content or "")
    return "".join(parts)


def _extract_table(tokens: list[Any], start: int) -> tuple[str, int]:
    """Extract a pipe-table as text. Returns (text, tokens_consumed)."""
    i = start + 1
    lines: list[str] = []
    while i < len(tokens):
        tok = tokens[i]
        if tok.type == "table_close":
            return "\n".join(lines), i - start + 1
        if tok.type == "tr_open":
            i += 1
            continue
        if tok.type == "tr_close":
            i += 1
            continue
        if tok.type in ("th_open", "td_open"):
            inline = tokens[i + 1] if i + 1 < len(tokens) else None
            cell = _inline_content(inline).strip()
            lines.append(cell)
            i += 3  # open, inline, close
            continue
        i += 1
    return "\n".join(lines), len(tokens) - start


_: BaseLoader = MarkdownLoader()

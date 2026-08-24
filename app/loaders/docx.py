"""DOCX loader using python-docx.

Extracts paragraphs (with heading styles), tables, and code-style paragraphs.
Page numbers are typically unavailable in DOCX and left ``None`` (spec §11.3).
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

# python-docx heading style names that indicate a title/section heading.
_HEADING_STYLES = {
    "Heading 1",
    "Heading 2",
    "Heading 3",
    "Heading 4",
    "Heading 5",
    "Heading 6",
    "Title",
    "Subtitle",
}
# Paragraph styles that indicate code blocks.
_CODE_STYLES = {"Source Code", "Code", "HTML Code", "Programlisting", "Verbatim"}


class DocxLoader:
    """python-docx-based loader for .docx files."""

    supported_extensions = frozenset({"docx"})

    def load(self, path: Path) -> ParsedDocument:
        try:
            import docx
        except ImportError as exc:
            raise LoaderError("python-docx is not installed", code="DEPENDENCY_MISSING") from exc

        if not path.exists():
            raise LoaderError(f"File not found: {path.name}", code="FILE_NOT_FOUND")

        document = docx.Document(str(path))
        paragraphs: list[Paragraph] = []
        current_line = 1
        title = ""

        body_elements = _iter_body_elements(document)

        for element in body_elements:
            if element["kind"] == "paragraph":
                p = element["obj"]
                style_name = p.style.name if p.style else ""
                raw = p.text
                text = normalize_text(raw).strip()
                if not text:
                    continue
                line_count = text.count("\n") + 1
                para_type: str = "text"
                meta: dict[str, Any] = {}
                if style_name in _HEADING_STYLES:
                    para_type = "markdown"
                    level = _heading_level(style_name)
                    meta["heading_level"] = level
                    meta["heading"] = text
                    if not title and style_name in ("Title", "Subtitle"):
                        title = text
                elif style_name in _CODE_STYLES:
                    para_type = "code"
                paragraphs.append(
                    Paragraph(
                        type=para_type,  # type: ignore[arg-type]
                        content=text,
                        page=None,
                        line_start=current_line,
                        line_end=current_line + line_count - 1,
                        metadata=meta,
                    )
                )
                current_line += line_count + 1
            elif element["kind"] == "table":
                table = element["obj"]
                text = _table_to_text(table)
                text = normalize_text(text).strip()
                if not text:
                    continue
                line_count = text.count("\n") + 1
                paragraphs.append(
                    Paragraph(
                        type="table",
                        content=text,
                        page=None,
                        line_start=current_line,
                        line_end=current_line + line_count - 1,
                        metadata={"rows": len(table.rows), "cols": len(table.columns)},
                    )
                )
                current_line += line_count + 1

        if not title:
            for p in paragraphs:
                if p.metadata.get("heading_level") == 1:
                    title = p.metadata.get("heading", "")
                    break
        if not title and paragraphs:
            title = paragraphs[0].content.split("\n", 1)[0][:300]

        total_chars = sum(len(p.content) for p in paragraphs)
        metadata = {
            "page_count": None,
            "character_count": total_chars,
            "loader": "python-docx",
            "paragraph_count": len(paragraphs),
        }
        return ParsedDocument(title=title, paragraphs=paragraphs, metadata=metadata)


def _iter_body_elements(document: Any) -> list[dict[str, Any]]:
    """Yield paragraphs and tables in document order."""
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph as DocxParagraph

    parent = document.element.body
    items = []
    for child in parent.iterchildren():
        if isinstance(child, CT_P):
            items.append({"kind": "paragraph", "obj": DocxParagraph(child, document)})
        elif isinstance(child, CT_Tbl):
            items.append({"kind": "table", "obj": Table(child, document)})
    return items


def _heading_level(style_name: str) -> int:
    if style_name in ("Title", "Subtitle"):
        return 1
    for i in range(1, 7):
        if style_name == f"Heading {i}":
            return i
    return 0


def _table_to_text(table: Any) -> str:
    """Serialize a table as pipe-separated rows with a header separator."""
    lines = []
    for i, row in enumerate(table.rows):
        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
        lines.append("| " + " | ".join(cells) + " |")
        if i == 0:
            lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
    return "\n".join(lines)


_: BaseLoader = DocxLoader()

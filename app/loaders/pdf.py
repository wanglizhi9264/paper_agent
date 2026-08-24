"""PDF loader using PyMuPDF (fitz).

Extracts text blocks per page, sorts them by reading order (top-to-bottom,
left-to-right within a vertical band), and emits ``Paragraph`` objects with
page numbers and normalized line ranges.

When a PDF has too little extractable text (empty-page ratio > 80% and total
characters < 200), raises ``OCREquiredError`` so the caller can surface a clear
message — OCR is out of MVP scope (spec §11.3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.loaders.base import (
    BaseLoader,
    LoaderError,
    OCREquiredError,
    Paragraph,
    ParsedDocument,
    normalize_text,
)

# Vertical band width (points) for grouping blocks on the same visual row.
_BAND_TOLERANCE = 12.0
# OCR trigger thresholds (spec §11.3).
_OCR_EMPTY_RATIO = 0.80
_OCR_MIN_CHARS = 200


class PdfLoader:
    """PyMuPDF-based loader for text-type PDFs."""

    supported_extensions = frozenset({"pdf"})

    def load(self, path: Path) -> ParsedDocument:
        try:
            import pymupdf
        except ImportError as exc:
            raise LoaderError("PyMuPDF is not installed", code="DEPENDENCY_MISSING") from exc

        if not path.exists():
            raise LoaderError(f"File not found: {path.name}", code="FILE_NOT_FOUND")

        doc = pymupdf.open(str(path))  # type: ignore[no-untyped-call]
        try:
            page_count = doc.page_count
            if page_count == 0:
                raise LoaderError("PDF has no pages", code="EMPTY_PDF")

            paragraphs: list[Paragraph] = []
            full_text_parts: list[str] = []
            current_line = 1
            empty_pages = 0

            for page_num in range(page_count):
                page = doc[page_num]
                blocks = _extract_sorted_blocks(page)
                page_text_parts: list[str] = []

                for b in blocks:
                    raw = b.get("text", "")
                    text = normalize_text(raw).strip()
                    if not text:
                        continue
                    line_count = text.count("\n") + 1
                    para = Paragraph(
                        type="text",
                        content=text,
                        page=page_num + 1,
                        line_start=current_line,
                        line_end=current_line + line_count - 1,
                        metadata={},
                    )
                    paragraphs.append(para)
                    page_text_parts.append(text)
                    full_text_parts.append(text)
                    current_line += line_count + 1  # +1 for blank separator

                if not page_text_parts:
                    empty_pages += 1

            total_chars = sum(len(p.content) for p in paragraphs)
            empty_ratio = empty_pages / page_count if page_count else 1.0
            if empty_ratio > _OCR_EMPTY_RATIO and total_chars < _OCR_MIN_CHARS:
                raise OCREquiredError(
                    f"PDF has too little extractable text "
                    f"({total_chars} chars, {empty_pages}/{page_count} empty pages). "
                    f"OCR is not supported in MVP.",
                )

            title = _extract_title(doc, paragraphs)
            metadata = {
                "page_count": page_count,
                "character_count": total_chars,
                "loader": "pymupdf",
            }
            return ParsedDocument(title=title, paragraphs=paragraphs, metadata=metadata)
        finally:
            doc.close()  # type: ignore[no-untyped-call]


def _extract_sorted_blocks(page: Any) -> list[dict[str, Any]]:
    """Return text blocks sorted by reading order (top-to-bottom, L-to-R).

    Uses ``get_text("blocks")`` which returns tuples
    ``(x0, y0, x1, y1, text, block_no, block_type)``. ``block_type`` 0 = text.
    """
    raw_blocks = page.get_text("blocks")
    text_blocks = []
    for b in raw_blocks:
        if len(b) < 7 or b[6] != 0:  # skip non-text blocks
            continue
        text = b[4].rstrip()
        if text:
            text_blocks.append({"text": text, "x": b[0], "y": b[1], "x1": b[2], "y1": b[3]})

    # Sort: group by vertical band, then left-to-right within band.
    text_blocks.sort(key=lambda b: (round(b["y"] / _BAND_TOLERANCE), b["x"]))
    return text_blocks


def _extract_title(doc: Any, paragraphs: list[Paragraph]) -> str:
    """Best-effort title: first non-empty paragraph, truncated to one line."""
    for p in paragraphs:
        first_line = p.content.split("\n", 1)[0].strip()
        if first_line:
            return first_line[:300]
    return ""


# Satisfy static protocol check.
_: BaseLoader = PdfLoader()

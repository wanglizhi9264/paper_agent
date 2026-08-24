"""Loader interfaces and shared types (spec §11).

All loaders produce a unified ``ParsedDocument`` regardless of source format.
Loaders must: normalize newlines to ``\\n``, remove NUL, preserve paragraph
order, retain page numbers when available, and compute normalized 1-based
line ranges. They never translate or summarize.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

ParagraphType = Literal["text", "markdown", "table", "code"]
JsonValue = Any


@dataclass(frozen=True, slots=True)
class Paragraph:
    """A single block extracted from a document.

    ``content`` is normalized text (newlines = ``\\n``, no NUL). ``page`` is
    1-based when available. ``line_start``/``line_end`` are 1-based line numbers
    in the normalized full-document text. ``metadata`` carries loader-specific
    but JSON-serializable info (e.g. heading level, code language).
    """

    type: ParagraphType
    content: str
    page: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Unified output of every loader.

    ``title`` is the best-effort document title; callers fall back to the
    de-extensioned filename when it is empty. ``paragraphs`` are in reading
    order. ``metadata`` holds document-level info (page_count, etc.).
    """

    title: str
    paragraphs: list[Paragraph]
    metadata: dict[str, JsonValue] = field(default_factory=dict)


class LoaderError(Exception):
    """Base loader error carrying a stable machine code."""

    code: str = "LOADER_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class OCREquiredError(LoaderError):
    """Raised when a PDF has too little extractable text (spec §11.3)."""

    code = "OCR_REQUIRED"


class UnsupportedExtensionError(LoaderError):
    code = "UNSUPPORTED_MEDIA_TYPE"


@runtime_checkable
class BaseLoader(Protocol):
    """Protocol implemented by every format-specific loader."""

    supported_extensions: frozenset[str]

    def load(self, path: Path) -> ParsedDocument: ...


def normalize_text(raw: str) -> str:
    """Normalize newlines to ``\\n`` and strip NUL bytes (spec §11.2)."""
    if not raw:
        return ""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "")
    return text

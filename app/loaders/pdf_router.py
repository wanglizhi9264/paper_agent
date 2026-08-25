"""PDF parser router (spec §7.4).

``auto`` runs the PyMuPDF fast path and accepts the candidate when every
§7.1 quality condition holds; otherwise it routes to the layout parser
(Docling, available from V2-3). MinerU is a challenger only and is never in
the auto chain (spec §7.4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from app.document_ir.errors import (
    PDF_PARSER_UNAVAILABLE,
    ParseError,
)
from app.document_ir.models import DocumentIR
from app.document_ir.protocol import DocumentParser
from app.loaders.pymupdf_adapter import PyMuPDFParser, fast_path_acceptable


class _AutoParser:
    """Fast-path first; falls through to the configured layout parser."""

    def __init__(self, settings: Any = None) -> None:
        self._settings = settings

    @property
    def manifest(self) -> Any:
        return PyMuPDFParser().manifest

    def parse(self, path: Path, *, document_id: UUID) -> DocumentIR:
        pymupdf = PyMuPDFParser()
        ir = pymupdf.parse(path, document_id=document_id)
        if fast_path_acceptable(ir, settings=self._settings):
            return ir
        return self._layout_parser().parse(path, document_id=document_id)

    def _layout_parser(self) -> DocumentParser:
        from app.core.config import get_settings

        settings = self._settings or get_settings()
        layout = getattr(settings, "pdf_layout_parser", "docling")
        raise ParseError(
            f"fast path rejected the candidate and layout parser '{layout}' is not available yet",
            code=PDF_PARSER_UNAVAILABLE,
        )


def get_pdf_parser(settings: Any | None = None) -> DocumentParser:
    """Return the parser selected by ``PAPER_RAG_PDF_PARSER`` (spec §7.4)."""
    if settings is None:
        from app.core.config import get_settings as _get

        settings = _get()
    name: str = getattr(settings, "pdf_parser", "pymupdf")
    if name == "pymupdf":
        return PyMuPDFParser()
    if name == "auto":
        return _AutoParser(settings)
    if name == "docling":
        raise ParseError(
            "Docling adapter is not implemented until V2-3",
            code=PDF_PARSER_UNAVAILABLE,
        )
    if name == "mineru":
        enabled = bool(getattr(settings, "mineru_enabled", False))
        if not enabled:
            raise ParseError(
                "MinerU selected but PAPER_RAG_MINERU_ENABLED is false",
                code=PDF_PARSER_UNAVAILABLE,
            )
        raise ParseError(
            "MinerU challenger adapter is not implemented until V2-4",
            code=PDF_PARSER_UNAVAILABLE,
        )
    raise ParseError(f"unknown pdf parser '{name}'", code=PDF_PARSER_UNAVAILABLE)


__all__ = ["get_pdf_parser"]

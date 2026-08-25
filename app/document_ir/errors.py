"""Stable error codes for PDF Ingestion V2 (spec §6.3)."""

from __future__ import annotations

PDF_PARSE_FAILED = "PDF_PARSE_FAILED"
PDF_LAYOUT_INVALID = "PDF_LAYOUT_INVALID"
PDF_TABLE_INVALID = "PDF_TABLE_INVALID"
PDF_UNICODE_CORRUPT = "PDF_UNICODE_CORRUPT"
PDF_READING_ORDER_LOW_CONFIDENCE = "PDF_READING_ORDER_LOW_CONFIDENCE"
PDF_PARSER_UNAVAILABLE = "PDF_PARSER_UNAVAILABLE"
PDF_PARSER_OOM = "PDF_PARSER_OOM"
OCR_REQUIRED = "OCR_REQUIRED"
IR_ARTIFACT_INVALID = "IR_ARTIFACT_INVALID"


class ParseError(Exception):
    """Parser failure carrying a stable machine code (spec §6.3)."""

    code: str = PDF_PARSE_FAILED

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code

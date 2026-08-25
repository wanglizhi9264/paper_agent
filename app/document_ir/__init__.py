"""Canonical Document IR (spec §5, §6, §8, §9).

This package is the only internal parsing contract. It must never import
loaders, ORM models, FastAPI, ARQ, FAISS, or LLM code.
"""

from __future__ import annotations

from app.document_ir.errors import (
    IR_ARTIFACT_INVALID,
    OCR_REQUIRED,
    PDF_LAYOUT_INVALID,
    PDF_PARSE_FAILED,
    PDF_PARSER_OOM,
    PDF_PARSER_UNAVAILABLE,
    PDF_READING_ORDER_LOW_CONFIDENCE,
    PDF_TABLE_INVALID,
    PDF_UNICODE_CORRUPT,
    ParseError,
)
from app.document_ir.markdown import (
    make_table_data,
    render_table_grid,
    render_table_markdown,
    table_fingerprint,
)
from app.document_ir.models import (
    BoundingBox,
    DocumentElement,
    DocumentIR,
    ElementKind,
    LayoutQualityReport,
    PageIR,
    ParserManifest,
    SourceSpan,
    TableCell,
    TableData,
)
from app.document_ir.normalize import (
    NORMALIZER_VERSION,
    NormalizationError,
    NormalizeResult,
    formula_search_aliases,
    normalize_for_retrieval,
)
from app.document_ir.protocol import DocumentParser, ParseCandidate
from app.document_ir.serialize import (
    canonical_json,
    compute_parser_signature,
    ir_sha256,
    manifest_signature,
    read_ir,
    write_ir,
)
from app.document_ir.validate import (
    BBOX_TOLERANCE,
    ValidationIssue,
    ValidationResult,
    validate_document_ir,
)

__all__ = [
    "BBOX_TOLERANCE",
    "IR_ARTIFACT_INVALID",
    "NORMALIZER_VERSION",
    "OCR_REQUIRED",
    "PDF_LAYOUT_INVALID",
    "PDF_PARSER_OOM",
    "PDF_PARSER_UNAVAILABLE",
    "PDF_PARSE_FAILED",
    "PDF_READING_ORDER_LOW_CONFIDENCE",
    "PDF_TABLE_INVALID",
    "PDF_UNICODE_CORRUPT",
    "BoundingBox",
    "DocumentElement",
    "DocumentIR",
    "DocumentParser",
    "ElementKind",
    "LayoutQualityReport",
    "NormalizationError",
    "NormalizeResult",
    "PageIR",
    "ParseCandidate",
    "ParseError",
    "ParserManifest",
    "SourceSpan",
    "TableCell",
    "TableData",
    "ValidationIssue",
    "ValidationResult",
    "canonical_json",
    "compute_parser_signature",
    "formula_search_aliases",
    "ir_sha256",
    "make_table_data",
    "manifest_signature",
    "normalize_for_retrieval",
    "read_ir",
    "render_table_grid",
    "render_table_markdown",
    "table_fingerprint",
    "validate_document_ir",
    "write_ir",
]

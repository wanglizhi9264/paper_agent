"""Real-model smoke tests for PDF Ingestion V2 (spec pdf-ingestion-v2 §18.3).

These run ONLY when explicitly requested::

    PAPER_RAG_RUN_MODEL_SMOKE=1 uv run pytest -m model_smoke tests/model_smoke

They never run in CI and never download models as an ordinary test side
effect; model weights are prepared by ``python -m app.cli.docling_setup``.
No private papers are referenced.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from app.document_ir.protocol import DocumentParser

pytestmark = [
    pytest.mark.model_smoke,
    pytest.mark.skipif(
        os.environ.get("PAPER_RAG_RUN_MODEL_SMOKE") != "1",
        reason="set PAPER_RAG_RUN_MODEL_SMOKE=1 to run real Docling smoke",
    ),
]

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "pdf_v2" / "simple_table.pdf"


def _parser() -> DocumentParser:
    pytest.importorskip("docling", reason="docling not installed; run `uv sync --extra pdf-layout`")
    from app.core.config import get_settings
    from app.loaders.docling_adapter import DoclingParser

    return cast(DocumentParser, DoclingParser.from_settings(get_settings()))


def test_docling_real_parse_public_fixture() -> None:
    parser = _parser()
    assert FIXTURE.exists(), "committed synthetic fixture missing"
    ir = parser.parse(FIXTURE, document_id=uuid4())

    assert ir.title.strip()
    assert len(ir.pages) == 1
    assert ir.elements, "no elements extracted"

    tables = [e for e in ir.elements if e.kind == "table"]
    assert len(tables) == 1, f"expected exactly one table, got {len(tables)}"
    table = tables[0].table
    assert table is not None
    assert (table.row_count, table.column_count) == (3, 3)
    header_texts = [
        cell.normalized_text.casefold() for cell in table.cells if cell.is_column_header
    ]
    assert header_texts[:3] == ["model", "is", "fid"]

    from app.document_ir.validate import validate_document_ir

    result = validate_document_ir(ir)
    assert not result.issues, [i.message for i in result.issues]

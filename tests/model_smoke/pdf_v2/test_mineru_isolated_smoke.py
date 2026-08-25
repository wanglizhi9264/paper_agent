from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.document_ir.validate import validate_document_ir
from app.loaders.mineru_adapter import MinerUParser


@pytest.mark.model_smoke
def test_mineru_isolated_public_fixture() -> None:
    if os.getenv("PAPER_RAG_RUN_MINERU_SMOKE") != "1":
        pytest.skip("set PAPER_RAG_RUN_MINERU_SMOKE=1 for isolated MinerU smoke")
    settings = get_settings()
    if not settings.mineru_enabled:
        pytest.fail("PAPER_RAG_MINERU_ENABLED must be true")
    uploads_fixture = settings.uploads_dir.resolve() / "model-smoke-simple-table.pdf"
    if not uploads_fixture.is_file():
        pytest.fail(
            "copy the public fixture to storage/uploads/model-smoke-simple-table.pdf "
            "before running the isolated smoke"
        )
    ir = MinerUParser.from_settings(settings).parse(uploads_fixture, document_id=uuid4())
    result = validate_document_ir(ir)
    assert not result.issues
    assert ir.quality.table_count == 1

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from app.document_ir.errors import PDF_PARSE_FAILED, PDF_PARSER_UNAVAILABLE, ParseError
from app.document_ir.validate import validate_document_ir
from app.loaders.mineru_adapter import (
    MinerUParser,
    build_mineru_argv,
    convert_mineru_payload,
)


def _payload() -> dict[str, object]:
    return {
        "pages": [{"page_idx": 0, "width": 612.0, "height": 792.0}],
        "elements": [
            {
                "id": "heading-1",
                "type": "text",
                "text": "Results",
                "text_level": 1,
                "page_idx": 0,
                "bbox": [72, 72, 200, 90],
            },
            {
                "id": "table-1",
                "type": "table",
                "table_caption": ["Table 1"],
                "table_body": (
                    "<table><tr><th>Model</th><th>IS</th><th>FID</th></tr>"
                    "<tr><td>Ours</td><td>9.46 ± 0.11</td><td>3.17</td></tr></table>"
                ),
                "page_idx": 0,
                "bbox": [72, 120, 500, 220],
            },
        ],
    }


def test_build_argv_is_shell_free_and_deterministic(tmp_path: Path) -> None:
    argv = build_mineru_argv(
        command="mineru",
        input_path=tmp_path / "paper;touch-pwned.pdf",
        output_dir=tmp_path / "out dir",
        backend="pipeline",
    )
    assert argv == [
        "mineru",
        "-p",
        str(tmp_path / "paper;touch-pwned.pdf"),
        "-o",
        str(tmp_path / "out dir"),
        "-b",
        "pipeline",
    ]


def test_fake_payload_converts_to_valid_ir_with_table_cells() -> None:
    ir = convert_mineru_payload(
        _payload(),
        document_id=uuid4(),
        parser_version="2.1.0",
        model_revisions={"pipeline": "a" * 40},
    )
    assert not validate_document_ir(ir).issues
    assert ir.parser.parser_id == "mineru"
    table = next(element.table for element in ir.elements if element.table is not None)
    assert (table.row_count, table.column_count, table.header_rows) == (2, 3, [0])
    assert [cell.normalized_text for cell in table.cells] == [
        "Model",
        "IS",
        "FID",
        "Ours",
        "9.46 ± 0.11",
        "3.17",
    ]
    assert all(cell.provenance[0].bbox is not None for cell in table.cells)


def test_input_must_be_inside_uploads(tmp_path: Path) -> None:
    uploads = tmp_path / "storage" / "uploads"
    uploads.mkdir(parents=True)
    outside = tmp_path / "private.pdf"
    outside.write_bytes(b"%PDF")
    parser = MinerUParser(
        storage_dir=tmp_path / "storage",
        command="mineru",
        backend="pipeline",
        timeout_seconds=10,
        parser_version="2.1.0",
        model_revision="a" * 40,
    )
    with pytest.raises(ParseError) as exc_info:
        parser.parse(outside, document_id=uuid4())
    assert exc_info.value.code == PDF_PARSER_UNAVAILABLE
    assert str(outside) not in str(exc_info.value)


def test_timeout_maps_to_stable_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    uploads = tmp_path / "storage" / "uploads"
    uploads.mkdir(parents=True)
    pdf = uploads / "paper.pdf"
    pdf.write_bytes(b"%PDF")

    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del args, kwargs
        raise subprocess.TimeoutExpired(cmd=["mineru"], timeout=1)

    monkeypatch.setattr(subprocess, "run", timeout)
    parser = MinerUParser(
        storage_dir=tmp_path / "storage",
        command="mineru",
        backend="pipeline",
        timeout_seconds=1,
        parser_version="2.1.0",
        model_revision="a" * 40,
    )
    with pytest.raises(ParseError) as exc_info:
        parser.parse(pdf, document_id=uuid4())
    assert exc_info.value.code == PDF_PARSE_FAILED
    assert "timed out" in str(exc_info.value)


def test_subprocess_result_is_loaded_without_logging_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = tmp_path / "storage"
    uploads = storage / "uploads"
    uploads.mkdir(parents=True)
    pdf = uploads / "paper.pdf"
    pdf.write_bytes(b"%PDF")

    def succeed(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del kwargs
        output = Path(argv[argv.index("-o") + 1])
        output.mkdir(parents=True)
        (output / "paper_content_list.json").write_text(
            json.dumps(_payload()), encoding="utf-8"
        )
        return subprocess.CompletedProcess(argv, 0, stdout=b"private text", stderr=b"")

    monkeypatch.setattr(subprocess, "run", succeed)
    parser = MinerUParser(
        storage_dir=storage,
        command="mineru",
        backend="pipeline",
        timeout_seconds=10,
        parser_version="2.1.0",
        model_revision="a" * 40,
    )
    ir = parser.parse(pdf, document_id=uuid4())
    assert ir.quality.table_count == 1
    assert not validate_document_ir(ir).issues

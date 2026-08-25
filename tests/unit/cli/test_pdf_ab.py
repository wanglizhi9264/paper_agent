"""A/B CLI end-to-end tests (spec §16).

The CLI is exercised through ``python -m app.cli.pdf_ab`` in a subprocess so
PyMuPDF parsing follows the same isolation convention as the loader tests
(macOS/arm64 segfault workaround).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from app.cli import pdf_ab
from tests.unit.document_ir.builders import make_ir, make_manifest

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "pdf_v2" / "simple_table.pdf"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app.cli.pdf_ab", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_pymupdf_run_writes_full_report(tmp_path: Path) -> None:
    out = tmp_path / "run-pymupdf"
    result = _run(
        [
            "--input",
            str(FIXTURE),
            "--parsers",
            "pymupdf",
            "--anchors",
            "Model,FID",
            "--output",
            str(out),
        ]
    )
    assert result.returncode == 0, result.stderr

    comparison = json.loads((out / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["input_name"] == "simple_table.pdf"
    assert len(comparison["input_sha256"]) == 64
    entry = comparison["results"]["pymupdf"]
    assert entry["ok"] is True
    assert entry["validator_ok"] is True
    assert entry["table_count"] == 1
    assert entry["elapsed_ms"] >= 0
    assert entry["parser_manifest"]["parser_id"] == "pymupdf"
    assert entry["anchors"]["found"] == ["Model", "FID"]

    for relative in (
        "manifest.json",
        "comparison.md",
        "pymupdf/document_ir.json",
        "pymupdf/document.md",
        "pymupdf/quality.json",
    ):
        assert (out / relative).exists(), f"missing {relative}"


def test_docling_candidate_writes_artifacts_without_real_models(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ir = make_ir(
        manifest=make_manifest(
            parser_id="docling",
            parser_version="2.121.0",
            model_revisions={"layout": "layout-sha", "table": "table-sha"},
        )
    )

    class FakeDoclingParser:
        def parse(self, _path: Path, *, document_id: object):
            del document_id
            return ir

    monkeypatch.setattr(pdf_ab, "_build_parser", lambda _name: FakeDoclingParser())
    entry = pdf_ab._run_one_parser(
        "docling",
        FIXTURE,
        uuid4(),
        tmp_path,
        ["Sample paragraph"],
        None,
    )

    assert entry["ok"] is True
    assert entry["validator_ok"] is True
    assert entry["parser_manifest"]["parser_id"] == "docling"
    assert entry["anchors"]["missing"] == []
    assert (tmp_path / "docling" / "document_ir.json").exists()
    assert (tmp_path / "docling" / "document.md").exists()
    assert (tmp_path / "docling" / "quality.json").exists()


def test_mineru_rejected_until_v2_4(tmp_path: Path) -> None:
    result = _run(
        ["--input", str(FIXTURE), "--parsers", "pymupdf,mineru", "--output", str(tmp_path)]
    )
    assert result.returncode == 2


def test_unknown_parser_rejected(tmp_path: Path) -> None:
    result = _run(["--input", str(FIXTURE), "--parsers", "bogus", "--output", str(tmp_path)])
    assert result.returncode == 2


def test_missing_input_rejected(tmp_path: Path) -> None:
    result = _run(
        ["--input", str(tmp_path / "nope.pdf"), "--parsers", "pymupdf", "--output", str(tmp_path)]
    )
    assert result.returncode == 2

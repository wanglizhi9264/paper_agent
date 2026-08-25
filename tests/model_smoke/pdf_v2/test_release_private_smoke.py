"""Explicit end-to-end release runner; private paths are supplied only by env."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from eval.pdf_v2_release import main

pytestmark = [
    pytest.mark.model_smoke,
    pytest.mark.skipif(
        os.environ.get("PAPER_RAG_RUN_PRIVATE_RELEASE_SMOKE") != "1",
        reason="set PAPER_RAG_RUN_PRIVATE_RELEASE_SMOKE=1 for the private 60-question gate",
    ),
]


def _required_path(name: str) -> Path:
    value = os.environ.get(name)
    assert value, f"{name} is required"
    path = Path(value)
    assert path.is_file(), f"{name} does not point to a file"
    return path


def test_private_release_gate(tmp_path: Path) -> None:
    exit_code = main(
        [
            "--dataset",
            str(_required_path("PAPER_RAG_PRIVATE_RESOLVED_DATASET")),
            "--hard-case-evidence",
            str(_required_path("PAPER_RAG_PRIVATE_HARD_CASE_EVIDENCE")),
            "--corpus-evidence",
            str(_required_path("PAPER_RAG_PRIVATE_CORPUS_EVIDENCE")),
            "--api-base",
            os.environ.get("PAPER_RAG_EVAL_API_BASE", "http://127.0.0.1:8000"),
            "--output",
            str(tmp_path / "release"),
            "--allow-live-api",
        ]
    )
    assert exit_code == 0

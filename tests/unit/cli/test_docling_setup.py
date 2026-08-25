"""Unit tests for the explicit Docling setup command.

All network, model downloads, and real parsing are replaced with deterministic
fakes. Ordinary tests must never fetch parser models (spec §15 and §18.1).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.cli import docling_setup


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    configured = SimpleNamespace(
        docling_layout_model="example/layout",
        docling_table_model="example/table",
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: configured)
    return configured


def test_skip_download_prints_pinned_env(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    settings: SimpleNamespace,
) -> None:
    del settings
    revisions = {"example/layout": "layout-sha", "example/table": "table-sha"}
    monkeypatch.setattr(docling_setup, "_resolve_revision", revisions.get)
    download_called = False

    def unexpected_download() -> bool:
        nonlocal download_called
        download_called = True
        return True

    monkeypatch.setattr(docling_setup, "_download_models", unexpected_download)

    assert docling_setup.main(["--skip-download"]) == 0
    assert download_called is False
    output = capsys.readouterr().out
    assert "PAPER_RAG_DOCLING_LAYOUT_REVISION=layout-sha" in output
    assert "PAPER_RAG_DOCLING_TABLE_REVISION=table-sha" in output


def test_download_failure_is_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    settings: SimpleNamespace,
) -> None:
    del settings
    monkeypatch.setattr(docling_setup, "_download_models", lambda: False)
    assert docling_setup.main([]) == 1


def test_unresolved_revision_is_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    settings: SimpleNamespace,
) -> None:
    del settings
    monkeypatch.setattr(docling_setup, "_resolve_revision", lambda _repo: None)
    assert docling_setup.main(["--skip-download"]) == 1
    assert "production activation requires pinned revisions" in capsys.readouterr().err


def test_check_parse_failure_is_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    settings: SimpleNamespace,
) -> None:
    del settings
    revisions = {"example/layout": "layout-sha", "example/table": "table-sha"}
    monkeypatch.setattr(docling_setup, "_resolve_revision", revisions.get)
    monkeypatch.setattr(docling_setup, "_check_parse", lambda _pdf: False)
    assert docling_setup.main(["--skip-download", "--check", "fixture.pdf"]) == 1


def test_check_parse_success(
    monkeypatch: pytest.MonkeyPatch,
    settings: SimpleNamespace,
) -> None:
    del settings
    revisions = {"example/layout": "layout-sha", "example/table": "table-sha"}
    monkeypatch.setattr(docling_setup, "_resolve_revision", revisions.get)
    monkeypatch.setattr(docling_setup, "_check_parse", lambda _pdf: True)
    assert docling_setup.main(["--skip-download", "--check", "fixture.pdf"]) == 0

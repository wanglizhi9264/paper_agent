"""Router tests (spec §7.4) and fast-path gate tests."""

from __future__ import annotations

from typing import Any

import pytest

from app.document_ir.errors import ParseError
from app.loaders.pdf_router import get_pdf_parser
from app.loaders.pymupdf_adapter import fast_path_acceptable
from tests.unit.document_ir.builders import make_element, make_ir, make_quality


class TestGetPdfParser:
    def test_pymupdf_selected(self) -> None:
        parser = get_pdf_parser(_settings(pdf_parser="pymupdf"))
        assert type(parser).__name__ == "PyMuPDFParser"

    def test_auto_returns_auto_parser(self) -> None:
        parser = get_pdf_parser(_settings(pdf_parser="auto"))
        assert type(parser).__name__ == "_AutoParser"

    def test_docling_selected_returns_docling_parser(self) -> None:
        parser = get_pdf_parser(_settings(pdf_parser="docling"))
        assert type(parser).__name__ == "DoclingParser"

    def test_auto_falls_back_to_docling_layout_parser(self) -> None:
        from app.loaders.pdf_router import _AutoParser

        auto = _AutoParser(_settings(pdf_parser="auto"))
        layout = auto._layout_parser()
        assert type(layout).__name__ == "DoclingParser"

    def test_auto_unknown_layout_parser_rejected(self) -> None:
        from app.document_ir.errors import PDF_PARSER_UNAVAILABLE
        from app.loaders.pdf_router import _AutoParser

        auto = _AutoParser(_settings(pdf_parser="auto", pdf_layout_parser="bogus"))
        with pytest.raises(ParseError) as exc_info:
            auto._layout_parser()
        assert exc_info.value.code == PDF_PARSER_UNAVAILABLE

    def test_mineru_disabled_rejected(self) -> None:
        with pytest.raises(ParseError):
            get_pdf_parser(_settings(pdf_parser="mineru", mineru_enabled=False))

    def test_mineru_enabled_still_unavailable_until_v2_4(self) -> None:
        with pytest.raises(ParseError) as exc_info:
            get_pdf_parser(_settings(pdf_parser="mineru", mineru_enabled=True))
        assert "V2-4" in str(exc_info.value)

    def test_settings_validation_rejects_unknown_parser(self) -> None:
        from app.core.config import Settings

        with pytest.raises(ValueError, match="PAPER_RAG_PDF_PARSER"):
            Settings(  # type: ignore[call-arg]
                database_url="postgresql+asyncpg://u:p@h/db",
                redis_url="redis://h:6379/0",
                pdf_parser="bogus",
            )


class TestFastPathGate:
    def test_clean_document_accepted(self) -> None:
        ir = make_ir(elements=[make_element()])
        assert fast_path_acceptable(ir)

    def test_hard_failures_rejected(self) -> None:
        ir = make_ir(
            elements=[make_element()],
            quality=make_quality(hard_failures=["boom"]),
        )
        assert not fast_path_acceptable(ir)

    def test_low_reading_order_rejected(self) -> None:
        ir = make_ir(
            elements=[make_element()],
            quality=make_quality(reading_order_confidence=0.5),
        )
        assert not fast_path_acceptable(ir)

    def test_malformed_table_rejected(self) -> None:
        ir = make_ir(
            elements=[make_element()],
            quality=make_quality(malformed_table_count=1),
        )
        assert not fast_path_acceptable(ir)

    def test_replacement_characters_rejected(self) -> None:
        ir = make_ir(
            elements=[make_element()],
            quality=make_quality(replacement_character_count=1),
        )
        assert not fast_path_acceptable(ir)

    def test_orphan_ratio_rejected(self) -> None:
        ir = make_ir(
            elements=[make_element()],
            quality=make_quality(orphan_numeric_ratio=0.5),
        )
        assert not fast_path_acceptable(ir)

    def test_settings_overrides_applied(self) -> None:
        ir = make_ir(
            elements=[make_element()],
            quality=make_quality(reading_order_confidence=0.9),
        )
        lenient = _settings(pdf_fast_path_min_reading_order_confidence=0.8)
        assert fast_path_acceptable(ir, settings=lenient)
        strict = _settings(pdf_fast_path_min_reading_order_confidence=0.99)
        assert not fast_path_acceptable(ir, settings=strict)


def _settings(**overrides: Any) -> Any:
    class S:
        pass

    defaults: dict[str, Any] = {
        "pdf_parser": "pymupdf",
        "pdf_layout_parser": "docling",
        "pdf_fast_path_min_reading_order_confidence": 0.95,
        "pdf_max_orphan_numeric_ratio": 0.05,
        "pdf_max_replacement_characters": 0,
        "mineru_enabled": False,
    }
    defaults.update(overrides)
    return type("S", (), defaults)()

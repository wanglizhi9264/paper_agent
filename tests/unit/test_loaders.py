from __future__ import annotations

from pathlib import Path

import pytest

from app.loaders.base import (
    LoaderError,
    normalize_text,
)
from app.loaders.docx import DocxLoader
from app.loaders.markdown import MarkdownLoader
from app.loaders.pdf import PdfLoader
from app.loaders.registry import (
    get_loader,
    get_loader_for_path,
    register_defaults,
    reset_registry,
    supported_extensions,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "files"


# --- normalize_text ---


def test_normalize_text_crlf() -> None:
    assert normalize_text("a\r\nb\rc") == "a\nb\nc"


def test_normalize_text_strips_nul() -> None:
    assert normalize_text("a\x00b") == "ab"


def test_normalize_text_empty() -> None:
    assert normalize_text("") == ""


# --- registry ---


def test_registry_defaults() -> None:
    register_defaults()
    assert supported_extensions() == frozenset({"pdf", "docx", "md"})


def test_registry_unknown_extension() -> None:
    register_defaults()
    with pytest.raises(LoaderError) as exc_info:
        get_loader("html")
    assert exc_info.value.code == "UNSUPPORTED_MEDIA_TYPE"


def test_registry_get_for_path() -> None:
    register_defaults()
    assert isinstance(get_loader_for_path(Path("a.pdf")), PdfLoader)
    assert isinstance(get_loader_for_path(Path("b.docx")), DocxLoader)
    assert isinstance(get_loader_for_path(Path("c.md")), MarkdownLoader)


def test_registry_reset() -> None:
    register_defaults()
    reset_registry()
    assert supported_extensions() == frozenset()
    register_defaults()


# --- markdown loader ---


def test_markdown_loader_parses_heading_and_code() -> None:
    loader = MarkdownLoader()
    md_path = FIXTURES / "sample.md"
    doc = loader.load(md_path)
    assert doc.title == "Attention Is All You Need"
    assert doc.metadata["loader"] == "markdown-it-py"

    kinds = [p.type for p in doc.paragraphs]
    assert "markdown" in kinds
    assert "code" in kinds

    code_paras = [p for p in doc.paragraphs if p.type == "code"]
    assert len(code_paras) == 1
    assert "def attention" in code_paras[0].content
    assert code_paras[0].metadata.get("language") == "python"

    heading_paras = [p for p in doc.paragraphs if p.metadata.get("heading_level")]
    levels = [p.metadata["heading_level"] for p in heading_paras]
    assert 1 in levels  # Introduction, Method, Results


def test_markdown_loader_line_ranges_monotonic() -> None:
    loader = MarkdownLoader()
    doc = loader.load(FIXTURES / "sample.md")
    for p in doc.paragraphs:
        assert p.line_start is not None
        assert p.line_end is not None
        assert p.line_start <= p.line_end


def test_markdown_loader_file_not_found() -> None:
    loader = MarkdownLoader()
    with pytest.raises(LoaderError, match="not found"):
        loader.load(Path("/nonexistent/file.md"))


def test_markdown_loader_html_disabled() -> None:
    """HTML in markdown must not be executed or rendered (spec §11.3)."""
    import tempfile

    loader = MarkdownLoader()
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write("# Title\n\n<script>alert(1)</script>\n\nText after.\n")
        path = Path(f.name)
    try:
        doc = loader.load(path)
        # The script tag should not produce a code paragraph or executable content.
        code_paras = [p for p in doc.paragraphs if p.type == "code"]
        assert all("script" not in p.content for p in code_paras)
    finally:
        path.unlink()


# --- PDF loader ---
# PyMuPDF segfaults inside the pytest process on some macOS/arm64 setups.
# Tests run the loader in a subprocess — closer to production (ARQ worker)
# and avoids the native extension conflict.


def test_pdf_loader_parses_text_pdf(tmp_path: Path) -> None:
    from tests.fixtures.generators import make_pdf
    from tests.fixtures.pdf_runner import load_pdf_in_subprocess

    pdf_path = make_pdf(tmp_path / "sample.pdf")
    doc = load_pdf_in_subprocess(pdf_path)
    assert doc["metadata"]["loader"] == "pymupdf"
    assert doc["metadata"]["page_count"] == 2
    assert len(doc["paragraphs"]) >= 2
    pages = {p["page"] for p in doc["paragraphs"]}
    assert 1 in pages
    assert 2 in pages
    assert "Sample Paper Title" in doc["title"] or "Introduction" in doc["title"]


def test_pdf_loader_ocr_required(tmp_path: Path) -> None:
    from tests.fixtures.generators import make_ocr_pdf
    from tests.fixtures.pdf_runner import load_pdf_expect_ocr_error

    pdf_path = make_ocr_pdf(tmp_path / "ocr.pdf")
    code = load_pdf_expect_ocr_error(pdf_path)
    assert code == "OCR_REQUIRED"


def test_pdf_loader_file_not_found() -> None:
    from tests.fixtures.pdf_runner import load_pdf_expect_ocr_error

    code = load_pdf_expect_ocr_error(Path("/nonexistent/file.pdf"))
    assert code == "FILE_NOT_FOUND"


# --- DOCX loader ---


def test_docx_loader_parses_headings_and_table(tmp_path: Path) -> None:
    from tests.fixtures.generators import make_docx

    docx_path = make_docx(tmp_path / "sample.docx")
    loader = DocxLoader()
    doc = loader.load(docx_path)
    assert doc.metadata["loader"] == "python-docx"
    assert doc.title == "Sample DOCX Title"

    heading_paras = [p for p in doc.paragraphs if p.metadata.get("heading_level")]
    assert any(p.metadata["heading_level"] == 1 for p in heading_paras)

    table_paras = [p for p in doc.paragraphs if p.type == "table"]
    assert len(table_paras) == 1
    assert "Metric" in table_paras[0].content
    assert "Accuracy" in table_paras[0].content

    # DOCX has no page numbers
    assert all(p.page is None for p in doc.paragraphs)


def test_docx_loader_file_not_found() -> None:
    loader = DocxLoader()
    with pytest.raises(LoaderError, match="not found"):
        loader.load(Path("/nonexistent/file.docx"))


# --- cross-format invariants (spec §11.2) ---


def test_all_loaders_produce_normalized_content(tmp_path: Path) -> None:
    """No NUL, no \\r in any paragraph content (spec §11.2)."""
    from tests.fixtures.generators import make_docx, make_pdf
    from tests.fixtures.pdf_runner import load_pdf_in_subprocess

    # Markdown
    doc = MarkdownLoader().load(FIXTURES / "sample.md")
    for p in doc.paragraphs:
        assert "\x00" not in p.content
        assert "\r" not in p.content

    # PDF (subprocess)
    pdf_doc = load_pdf_in_subprocess(make_pdf(tmp_path / "s.pdf"))
    for p in pdf_doc["paragraphs"]:
        assert "\x00" not in p["content"]
        assert "\r" not in p["content"]

    # DOCX
    docx_doc = DocxLoader().load(make_docx(tmp_path / "s.docx"))
    for p in docx_doc.paragraphs:
        assert "\x00" not in p.content
        assert "\r" not in p.content


def test_all_loaders_preserve_order(tmp_path: Path) -> None:
    """Paragraphs must be in reading order (spec §11.2)."""
    from tests.fixtures.generators import make_pdf
    from tests.fixtures.pdf_runner import load_pdf_in_subprocess

    doc = load_pdf_in_subprocess(make_pdf(tmp_path / "order.pdf"))
    for i in range(1, len(doc["paragraphs"])):
        prev = doc["paragraphs"][i - 1]
        curr = doc["paragraphs"][i]
        if prev["page"] is not None and curr["page"] is not None:
            assert (prev["page"], prev["line_start"] or 0) <= (
                curr["page"],
                curr["line_start"] or 0,
            )

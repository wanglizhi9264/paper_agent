from __future__ import annotations

import hashlib

from app.chunking.models import ChunkConfig
from app.chunking.pipeline import chunk_document
from app.loaders.base import Paragraph, ParsedDocument


def _md_doc(paras: list[Paragraph], title: str = "Test") -> ParsedDocument:
    return ParsedDocument(title=title, paragraphs=paras)


def _heading(text: str, level: int) -> Paragraph:
    return Paragraph(
        type="markdown", content=text, metadata={"heading_level": level, "heading": text}
    )


def _text(text: str, page: int | None = 1) -> Paragraph:
    return Paragraph(type="text", content=text, page=page)


def _code(text: str, lang: str = "python") -> Paragraph:
    return Paragraph(type="code", content=text, metadata={"language": lang})


def _table(text: str) -> Paragraph:
    return Paragraph(type="table", content=text)


def test_small_document_single_chunk() -> None:
    doc = _md_doc([_text("Short content.")])
    results = chunk_document(doc)
    assert len(results) == 1
    assert results[0].kind == "text"
    assert results[0].chunk_index == 0
    assert "Short content." in results[0].raw_content


def test_small_document_includes_title_and_section() -> None:
    doc = _md_doc([_heading("Intro", 1), _text("Body text.")], title="My Paper")
    results = chunk_document(doc)
    assert len(results) == 1
    assert "My Paper" in results[0].retrieval_content
    assert "Intro" in results[0].retrieval_content


def test_large_document_splits_into_multiple_chunks() -> None:
    # Create > 2048 chars to bypass small-doc shortcut.
    long_text = "This is a sentence. " * 200  # ~4000 chars
    doc = _md_doc([_text(long_text)])
    results = chunk_document(doc)
    assert len(results) > 1
    # All chunks should be text type
    assert all(r.kind == "text" for r in results)
    # chunk_index should be 0..N-1
    for i, r in enumerate(results):
        assert r.chunk_index == i


def test_title_chunks_generated_when_enabled() -> None:
    paras = [
        _heading("Section A", 1),
        _text("a" * 3000),
        _heading("Section B", 1),
        _text("b" * 3000),
    ]
    doc = _md_doc(paras, title="Paper")
    results = chunk_document(doc)
    title_chunks = [r for r in results if r.kind == "title"]
    assert len(title_chunks) == 2
    assert title_chunks[0].raw_content == "Section A"
    assert title_chunks[1].raw_content == "Section B"


def test_title_chunks_suppressed_when_disabled() -> None:
    paras = [
        _heading("Section A", 1),
        _text("a" * 3000),
    ]
    doc = _md_doc(paras)
    cfg = ChunkConfig(title_chunk_on=False)
    results = chunk_document(doc, cfg)
    title_chunks = [r for r in results if r.kind == "title"]
    assert len(title_chunks) == 0


def test_table_chunk_preserved() -> None:
    table_text = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    paras = [_text("x" * 3000), _table(table_text)]
    doc = _md_doc(paras)
    results = chunk_document(doc)
    table_chunks = [r for r in results if r.kind == "table"]
    assert len(table_chunks) == 1
    assert "| A | B |" in table_chunks[0].raw_content
    assert "| 1 | 2 |" in table_chunks[0].raw_content


def test_table_split_repeats_header() -> None:
    """Large tables split into fragments, each repeating the header (spec §12.3)."""
    header = "| Metric | Value |"
    sep = "| --- | --- |"
    rows = [f"| m{i} | {i} |" for i in range(100)]
    table_text = "\n".join([header, sep] + rows)
    paras = [_table(table_text)]
    doc = _md_doc(paras)
    cfg = ChunkConfig(max_chunk_chars=200, small_document_char_threshold=50)
    results = chunk_document(doc, cfg)
    table_chunks = [r for r in results if r.kind == "table"]
    assert len(table_chunks) > 1
    # Every fragment must contain the header
    for tc in table_chunks:
        assert "| Metric | Value |" in tc.raw_content


def test_code_chunk_stored() -> None:
    code = "def foo():\n    return 42"
    paras = [_text("x" * 3000), _code(code)]
    doc = _md_doc(paras)
    results = chunk_document(doc)
    code_chunks = [r for r in results if r.kind == "code"]
    assert len(code_chunks) == 1
    assert "def foo" in code_chunks[0].raw_content
    assert code_chunks[0].metadata.get("language") == "python"


def test_code_not_add_index_flag() -> None:
    code = "secret = 'key'"
    paras = [_text("x" * 3000), _code(code)]
    doc = _md_doc(paras)
    cfg = ChunkConfig(code_not_add_index=True)
    results = chunk_document(doc, cfg)
    code_chunks = [r for r in results if r.kind == "code"]
    assert len(code_chunks) == 1
    assert code_chunks[0].metadata.get("code_not_add_index") is True
    assert code_chunks[0].add_to_index is False


def test_content_hash_is_sha256() -> None:
    doc = _md_doc([_text("hello")])
    results = chunk_document(doc)
    expected = hashlib.sha256(b"hello").hexdigest()
    assert results[0].content_hash == expected


def test_deterministic_same_output_on_repeat() -> None:
    paras = [
        _heading("A", 1),
        _text("a" * 3000),
        _heading("B", 2),
        _text("b" * 3000),
    ]
    doc = _md_doc(paras)
    r1 = chunk_document(doc)
    r2 = chunk_document(doc)
    assert len(r1) == len(r2)
    for a, b in zip(r1, r2, strict=True):
        assert a.chunk_index == b.chunk_index
        assert a.raw_content == b.raw_content
        assert a.content_hash == b.content_hash
        assert a.retrieval_content == b.retrieval_content
        assert a.kind == b.kind


def test_retrieval_content_format() -> None:
    """retrieval_content = title + section_path + raw_content (spec §12.2 rule 5)."""
    paras = [_heading("Intro", 1), _text("body text")]
    doc = _md_doc(paras, title="MyPaper")
    results = chunk_document(doc)
    # Small doc: single chunk
    rc = results[0].retrieval_content
    assert "MyPaper" in rc
    assert "Intro" in rc
    assert "body text" in rc


def test_section_path_captured() -> None:
    paras = [
        _heading("Top", 1),
        _text("x" * 3000),
        _heading("Sub", 2),
        _text("y" * 3000),
    ]
    doc = _md_doc(paras)
    results = chunk_document(doc)
    # Find a content chunk under Sub
    sub_chunks = [r for r in results if "Sub" in r.section_path]
    assert len(sub_chunks) > 0
    assert sub_chunks[0].section_path == ["Top", "Sub"]


def test_chunk_order_matches_document_order() -> None:
    paras = [
        _heading("A", 1),
        _text("aaa " * 600),
        _heading("B", 1),
        _text("bbb " * 600),
    ]
    doc = _md_doc(paras)
    results = chunk_document(doc)
    # Title A comes before content of A, which comes before Title B
    kinds = [r.kind for r in results]
    assert kinds[0] == "title"  # Title A
    # Find first 'title' and second 'title'
    title_indices = [i for i, k in enumerate(kinds) if k == "title"]
    assert len(title_indices) == 2
    assert title_indices[0] < title_indices[1]


def test_consecutive_pdf_text_blocks_are_merged_before_sentence_chunking() -> None:
    repeated = "context " * 280
    phrase_a = Paragraph(type="text", content="denoising diffusion", page=5)
    phrase_b = Paragraph(type="text", content="probabilistic models improve synthesis.", page=5)
    doc = _md_doc([_text(repeated), phrase_a, phrase_b])

    results = chunk_document(doc)

    assert any("denoising diffusion probabilistic models" in chunk.raw_content for chunk in results)
    assert all(chunk.page_start == chunk.page_end for chunk in results if chunk.page_start)

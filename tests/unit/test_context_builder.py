from __future__ import annotations

from app.context.builder import SourceBlock, build_citation_map, format_source_block, pack_context
from app.retrieval.fusion import RetrievalResult


def _make_result(
    chunk_id: str,
    document_id: str = "doc-1",
    section_path: list[str] | None = None,
    raw_content: str = "content",
    page_start: int | None = 1,
    expanded_from: str | None = None,
) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        faiss_id=0,
        score=1.0,
        source="rrf" if not expanded_from else "expanded",
        rank=1,
        document_id=document_id,
        section_path=section_path or ["Intro"],
        raw_content=raw_content,
        retrieval_content=raw_content,
        page_start=page_start,
        expanded_from_chunk_id=expanded_from,
    )


def test_pack_context_basic() -> None:
    results = [_make_result("c1", raw_content="hello world")]
    blocks = pack_context(results, document_titles={"doc-1": "My Paper"})
    assert len(blocks) == 1
    assert blocks[0].index == 1
    assert blocks[0].chunk_id == "c1"
    assert blocks[0].document_title == "My Paper"
    assert "hello world" in blocks[0].content


def test_pack_context_multiple_sources() -> None:
    results = [
        _make_result("c1", document_id="d1"),
        _make_result("c2", document_id="d2"),
        _make_result("c3", document_id="d3"),
    ]
    blocks = pack_context(results)
    assert len(blocks) == 3
    assert blocks[0].index == 1
    assert blocks[1].index == 2
    assert blocks[2].index == 3


def test_pack_context_truncation() -> None:
    long_content = "a" * 10000 + ". more text."
    results = [_make_result("c1", raw_content=long_content)]
    blocks = pack_context(results, budget_tokens=100, tokens_per_char=1.0)
    assert blocks[0].truncated
    assert len(blocks[0].content) < len(long_content)


def test_pack_context_truncation_sentence_boundary() -> None:
    content = "First sentence. Second sentence. Third sentence."
    results = [_make_result("c1", raw_content=content)]
    blocks = pack_context(results, budget_tokens=30, tokens_per_char=1.0)
    # Should cut at sentence boundary
    if blocks[0].truncated:
        assert blocks[0].content.endswith(".")


def test_pack_context_budget_stops() -> None:
    results = [_make_result(f"c{i}", raw_content="a" * 1000) for i in range(10)]
    blocks = pack_context(results, budget_tokens=500, tokens_per_char=1.0)
    # Should not include all 10
    assert len(blocks) < 10


def test_format_source_block() -> None:
    block = SourceBlock(
        index=1,
        chunk_id="abc-123",
        document_title="My Paper",
        section_path=["Intro", "Background"],
        page="6",
        content="Some content.",
    )
    text = format_source_block(block)
    assert "[Source 1]" in text
    assert "My Paper" in text
    assert "Intro > Background" in text
    assert "Page: 6" in text
    assert "abc-123" in text
    assert "Some content." in text


def test_build_citation_map() -> None:
    blocks = [
        SourceBlock(
            index=1, chunk_id="a", document_title="T", section_path=[], page="1", content=""
        ),
        SourceBlock(
            index=2, chunk_id="b", document_title="T", section_path=[], page="1", content=""
        ),
    ]
    cmap = build_citation_map(blocks)
    assert cmap == {1: "a", 2: "b"}


def test_pack_context_merge_adjacent() -> None:
    results = [
        _make_result("c1", document_id="d1", section_path=["S1"]),
        _make_result("c2", document_id="d1", section_path=["S1"], expanded_from="c1"),
    ]
    blocks = pack_context(results, merge_adjacent=True)
    assert len(blocks) == 1
    assert "c1" in blocks[0].chunk_id  # original chunk's id
    assert "content" in blocks[0].content  # merged content

from __future__ import annotations

from app.context.dedup import dedup, dedup_by_chunk_id, dedup_by_content_hash, neighbor_expansion
from app.retrieval.fusion import RetrievalResult


def _make_result(
    chunk_id: str,
    faiss_id: int = 0,
    document_id: str = "doc-1",
    section_path: list[str] | None = None,
    raw_content: str = "content",
    content_hash: str = "",
) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        faiss_id=faiss_id,
        score=1.0,
        source="rrf",
        rank=1,
        document_id=document_id,
        section_path=section_path or ["Intro"],
        raw_content=raw_content,
        retrieval_content=raw_content,
        metadata={"content_hash": content_hash} if content_hash else None,
    )


def test_dedup_by_chunk_id_removes_duplicates() -> None:
    r1 = _make_result("a", 1)
    r2 = _make_result("a", 1)
    r3 = _make_result("b", 2)
    result = dedup_by_chunk_id([r1, r2, r3])
    assert len(result) == 2
    assert result[0].chunk_id == "a"
    assert result[1].chunk_id == "b"


def test_dedup_by_content_hash() -> None:
    r1 = _make_result("a", 1, content_hash="hash1")
    r2 = _make_result("b", 2, content_hash="hash1")
    r3 = _make_result("c", 3, content_hash="hash2")
    result = dedup_by_content_hash([r1, r2, r3])
    assert len(result) == 2
    assert result[0].chunk_id == "a"
    assert result[1].chunk_id == "c"


def test_dedup_full() -> None:
    r1 = _make_result("a", 1, content_hash="hash1")
    r2 = _make_result("a", 1, content_hash="hash1")
    r3 = _make_result("b", 2, content_hash="hash1")
    result = dedup([r1, r2, r3])
    assert len(result) == 1
    assert result[0].chunk_id == "a"


def test_neighbor_expansion_adds_adjacent() -> None:
    r1 = _make_result("c1", document_id="d1", section_path=["S1"])
    chunks_by_doc = {
        "d1": [
            _make_result("c0", document_id="d1", section_path=["S1"]),
            r1,
            _make_result("c2", document_id="d1", section_path=["S1"]),
            _make_result("c3", document_id="d1", section_path=["S2"]),
        ]
    }
    expanded = neighbor_expansion([r1], chunks_by_doc, window=1)
    ids = [r.chunk_id for r in expanded]
    assert "c1" in ids
    assert "c0" in ids  # left neighbor
    assert "c2" in ids  # right neighbor
    assert "c3" not in ids  # different section


def test_neighbor_expansion_no_duplicates() -> None:
    r1 = _make_result("c1", document_id="d1")
    r2 = _make_result("c2", document_id="d1")
    chunks_by_doc = {
        "d1": [
            _make_result("c0", document_id="d1"),
            r1,
            r2,
            _make_result("c3", document_id="d1"),
        ]
    }
    expanded = neighbor_expansion([r1, r2], chunks_by_doc, window=1)
    ids = [r.chunk_id for r in expanded]
    # c0 expanded from c1; c3 expanded from c2
    assert "c0" in ids
    assert "c3" in ids
    # No duplicates
    assert len(ids) == len(set(ids))


def test_neighbor_expansion_marked_source() -> None:
    r1 = _make_result("c1", document_id="d1")
    chunks_by_doc = {
        "d1": [
            _make_result("c0", document_id="d1"),
            r1,
        ]
    }
    expanded = neighbor_expansion([r1], chunks_by_doc, window=1)
    exp = [r for r in expanded if r.source == "expanded"]
    assert len(exp) == 1
    assert exp[0].expanded_from_chunk_id == "c1"
    assert exp[0].score == 0.0


def test_neighbor_expansion_window_zero() -> None:
    r1 = _make_result("c1", document_id="d1")
    chunks_by_doc = {
        "d1": [_make_result("c0", document_id="d1"), r1, _make_result("c2", document_id="d1")]
    }
    expanded = neighbor_expansion([r1], chunks_by_doc, window=0)
    assert len(expanded) == 1

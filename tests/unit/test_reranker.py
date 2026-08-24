from __future__ import annotations

from app.rerank.base import FakeReranker


def test_fake_reranker_basic() -> None:
    r = FakeReranker()
    query = "deep learning model"
    passages = [
        "deep learning architecture",
        "natural language processing",
        "machine learning model",
    ]
    scores = r.rerank(query, passages)
    assert len(scores) == 3
    # First passage has highest overlap
    assert scores[0] >= scores[1]
    assert scores[2] >= scores[1]


def test_fake_reranker_perfect_match() -> None:
    r = FakeReranker()
    scores = r.rerank("hello world", ["hello world"])
    assert scores[0] == 1.0


def test_fake_reranker_no_overlap() -> None:
    r = FakeReranker()
    scores = r.rerank("alpha beta", ["gamma delta"])
    assert scores[0] == 0.0


def test_fake_reranker_empty_passages() -> None:
    r = FakeReranker()
    scores = r.rerank("test", [])
    assert scores == []


def test_fake_reranker_deterministic() -> None:
    r = FakeReranker()
    s1 = r.rerank("deep learning", ["learning deep", "machine learning"])
    s2 = r.rerank("deep learning", ["learning deep", "machine learning"])
    assert s1 == s2


def test_fake_reranker_empty_query() -> None:
    r = FakeReranker()
    scores = r.rerank("", ["some text"])
    assert scores[0] == 0.0

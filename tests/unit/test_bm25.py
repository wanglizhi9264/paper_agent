from __future__ import annotations

from app.retrieval.bm25 import BM25Index


def _build_simple_index() -> BM25Index:
    docs = [
        (0, "machine learning deep learning model"),
        (1, "natural language processing text"),
        (2, "deep learning neural network architecture"),
        (3, "computer vision image classification"),
    ]
    idx = BM25Index()
    idx.build(docs)
    return idx


def test_bm25_build_stats() -> None:
    idx = _build_simple_index()
    s = idx.stats
    assert s.n_docs == 4
    assert s.avgdl > 0
    assert s.df.get("learning") == 2  # appears in doc 0 and 2
    assert s.df.get("model") == 1
    assert s.doc_len[0] == 5  # 5 tokens


def test_bm25_search_basic() -> None:
    idx = _build_simple_index()
    results = idx.search("deep learning", top_k=4)
    assert len(results) > 0
    # Docs containing "deep learning" should rank top
    top_ids = [doc_id for doc_id, _ in results]
    assert 0 in top_ids
    assert 2 in top_ids


def test_bm25_score_ordering() -> None:
    idx = _build_simple_index()
    results = idx.search("deep learning model", top_k=4)
    # Doc 0 has both "deep" "learning" "model" — should rank high
    assert results[0][0] in (0, 2)  # one of the docs with "deep learning"


def test_bm25_no_match() -> None:
    idx = _build_simple_index()
    results = idx.search("quantum computing", top_k=4)
    assert len(results) == 0


def test_bm25_scope_filter() -> None:
    idx = _build_simple_index()
    results = idx.search("deep learning", top_k=4, scope_doc_ids={2, 3})
    top_ids = [doc_id for doc_id, _ in results]
    assert 0 not in top_ids  # excluded by scope
    assert 2 in top_ids


def test_bm25_minimum_should_match() -> None:
    idx = _build_simple_index()
    # Query has 3 unique terms; require at least 2 to match
    results = idx.search("deep learning quantum", top_k=4, minimum_should_match=2)
    top_ids = [doc_id for doc_id, _ in results]
    # Only docs with both "deep" and "learning" should match
    assert 0 in top_ids
    assert 2 in top_ids
    assert 3 not in top_ids  # doesn't have "deep" or "learning"


def test_bm25_minimum_should_match_exceeds_terms() -> None:
    idx = _build_simple_index()
    # Query has 1 unique term; min_match=5 → min(5,1)=1
    results = idx.search("learning", top_k=4, minimum_should_match=5)
    assert len(results) > 0


def test_bm25_idf_formula() -> None:
    """Verify IDF = log((N - df + 0.5) / (df + 0.5) + 1) (spec §13.2)."""
    import math

    idx = _build_simple_index()
    idf = idx.idf
    N = 4
    df = idx.stats.df.get("learning", 0)
    expected = math.log((N - df + 0.5) / (df + 0.5) + 1)
    assert abs(idf["learning"] - expected) < 1e-10


def test_bm25_serialization() -> None:
    idx = _build_simple_index()
    d = idx.to_dict()
    restored = BM25Index.from_dict(d)
    assert restored.stats.n_docs == idx.stats.n_docs
    assert restored.stats.df == idx.stats.df
    assert restored.stats.doc_len == idx.stats.doc_len
    # Search results should be identical
    r1 = idx.search("deep learning", top_k=4)
    r2 = restored.search("deep learning", top_k=4)
    assert r1 == r2


def test_bm25_rebuild_recalculates() -> None:
    """Spec §13.2: must recalculate N, df, doc_len, avgdl from scratch."""
    idx = BM25Index()
    idx.build([(0, "alpha beta"), (1, "beta gamma")])
    assert idx.stats.n_docs == 2
    assert idx.stats.df.get("beta") == 2
    idx.build([(0, "delta"), (1, "epsilon"), (2, "zeta")])
    assert idx.stats.n_docs == 3
    assert idx.stats.df.get("beta") is None  # old df wiped
    assert idx.stats.df.get("delta") == 1

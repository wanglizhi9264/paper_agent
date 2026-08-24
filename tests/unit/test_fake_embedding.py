from __future__ import annotations

import numpy as np

from app.embedding.fake import FakeEmbeddingAdapter


def test_fake_embedding_deterministic() -> None:
    adapter = FakeEmbeddingAdapter(dimension=32)
    texts = ["hello world", "machine learning"]
    r1 = adapter.embed_texts(texts)
    r2 = adapter.embed_texts(texts)
    assert r1.dimension == 32
    assert r1.vectors.shape == (2, 32)
    np.testing.assert_array_equal(r1.vectors, r2.vectors)


def test_fake_embedding_different_texts_different_vectors() -> None:
    adapter = FakeEmbeddingAdapter(dimension=32)
    r = adapter.embed_texts(["hello world", "goodbye moon"])
    assert not np.array_equal(r.vectors[0], r.vectors[1])


def test_fake_embedding_normalized() -> None:
    adapter = FakeEmbeddingAdapter(dimension=32)
    r = adapter.embed_texts(["hello world machine learning"])
    norms = np.linalg.norm(r.vectors, axis=1)
    assert np.allclose(norms, 1.0)


def test_fake_embedding_empty_list() -> None:
    adapter = FakeEmbeddingAdapter(dimension=32)
    r = adapter.embed_texts([])
    assert r.vectors.shape == (0, 32)
    assert r.dimension == 32


def test_fake_embedding_embed_query() -> None:
    adapter = FakeEmbeddingAdapter(dimension=32)
    r = adapter.embed_query("what is deep learning")
    assert r.vectors.shape == (1, 32)


def test_fake_embedding_manifest() -> None:
    adapter = FakeEmbeddingAdapter(dimension=64)
    m = adapter.manifest
    assert m.dimension == 64
    assert m.model_id == "fake-embedding-v1"
    assert m.normalize is True
    assert m.signature  # non-empty


def test_fake_embedding_shared_tokens_higher_similarity() -> None:
    """Two texts sharing more tokens should have higher cosine similarity."""
    adapter = FakeEmbeddingAdapter(dimension=128)
    r = adapter.embed_texts(
        ["deep learning model", "deep learning model architecture", "completely different topic"]
    )
    v0, v1, v2 = r.vectors
    sim_01 = float(np.dot(v0, v1))
    sim_02 = float(np.dot(v0, v2))
    assert sim_01 > sim_02


def test_fake_embedding_no_query_prefix() -> None:
    """Fake adapter has no prefix — query and passage embeddings are identical."""
    adapter = FakeEmbeddingAdapter(dimension=32)
    rq = adapter.embed_query("hello world")
    rp = adapter.embed_texts(["hello world"], is_query=False)
    np.testing.assert_array_equal(rq.vectors[0], rp.vectors[0])

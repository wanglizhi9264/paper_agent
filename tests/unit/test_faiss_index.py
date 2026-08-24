from __future__ import annotations

import numpy as np
import pytest

from app.embedding.base import ZeroVectorError
from app.index.faiss_index import FaissIndex, FaissIndexError


def test_create_and_search() -> None:
    idx = FaissIndex.create(dimension=4)
    assert idx.dimension == 4
    assert idx.ntotal == 0

    vecs = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32)
    ids = np.array([0, 1], dtype=np.int64)
    idx.add(vecs, ids)
    assert idx.ntotal == 2

    query = np.array([1, 0, 0, 0], dtype=np.float32)
    scores, result_ids = idx.search(query, top_k=2)
    assert result_ids[0] == 0
    assert scores[0] > scores[1]


def test_search_batch() -> None:
    idx = FaissIndex.create(dimension=4)
    vecs = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]], dtype=np.float32)
    ids = np.array([10, 20, 30], dtype=np.int64)
    idx.add(vecs, ids)

    queries = np.array([[1, 0, 0, 0], [0, 0, 1, 0]], dtype=np.float32)
    results = idx.search_batch(queries, top_k=1)
    assert results[0][1][0] == 10  # first query matches id=10
    assert results[1][1][0] == 30  # second query matches id=30


def test_add_texts_auto_normalize() -> None:
    idx = FaissIndex.create(dimension=4)
    vecs = np.array([[3, 0, 0, 0], [0, 5, 0, 0]], dtype=np.float32)
    ids = np.array([0, 1], dtype=np.int64)
    idx.add_texts(vecs, ids, normalize=True)
    assert idx.ntotal == 2
    # Search with normalized query
    query = np.array([1, 0, 0, 0], dtype=np.float32)
    scores, result_ids = idx.search(query, top_k=2)
    assert result_ids[0] == 0


def test_add_texts_zero_vector_rejected() -> None:
    idx = FaissIndex.create(dimension=4)
    vecs = np.array([[0, 0, 0, 0]], dtype=np.float32)
    ids = np.array([0], dtype=np.int64)
    with pytest.raises(ZeroVectorError):
        idx.add_texts(vecs, ids, normalize=True)


def test_dimension_mismatch_on_add() -> None:
    idx = FaissIndex.create(dimension=4)
    vecs = np.array([[1, 0, 0]], dtype=np.float32)
    ids = np.array([0], dtype=np.int64)
    with pytest.raises(FaissIndexError, match="dimension mismatch"):
        idx.add(vecs, ids)


def test_dimension_mismatch_on_search() -> None:
    idx = FaissIndex.create(dimension=4)
    vecs = np.array([[1, 0, 0, 0]], dtype=np.float32)
    ids = np.array([0], dtype=np.int64)
    idx.add(vecs, ids)
    query = np.array([1, 0, 0], dtype=np.float32)
    with pytest.raises(FaissIndexError, match="query dimension mismatch"):
        idx.search(query, top_k=1)


def test_save_and_load(tmp_path) -> None:
    idx = FaissIndex.create(dimension=8)
    vecs = np.random.rand(10, 8).astype(np.float32)
    vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
    ids = np.arange(10, dtype=np.int64)
    idx.add(vecs, ids)

    path = tmp_path / "index.faiss"
    idx.save(path)
    assert path.exists()

    loaded = FaissIndex.load(path)
    assert loaded.dimension == 8
    assert loaded.ntotal == 10

    query = vecs[0]
    scores, result_ids = loaded.search(query, top_k=3)
    assert result_ids[0] == 0


def test_load_with_expected_dimension(tmp_path) -> None:
    idx = FaissIndex.create(dimension=8)
    vecs = np.array([[1] + [0] * 7], dtype=np.float32)
    ids = np.array([0], dtype=np.int64)
    idx.add(vecs, ids)
    path = tmp_path / "index.faiss"
    idx.save(path)

    loaded = FaissIndex.load(path, expected_dimension=8)
    assert loaded.dimension == 8

    with pytest.raises(FaissIndexError, match="dimension mismatch"):
        FaissIndex.load(path, expected_dimension=16)


def test_save_atomic_rename(tmp_path) -> None:
    idx = FaissIndex.create(dimension=4)
    vecs = np.array([[1, 0, 0, 0]], dtype=np.float32)
    ids = np.array([0], dtype=np.int64)
    idx.add(vecs, ids)
    path = tmp_path / "index.faiss"
    idx.save(path)
    assert not path.with_suffix(".tmp").exists()
    assert path.exists()


def test_restart_top_k_consistency(tmp_path) -> None:
    """Spec: restart (reload) gives same top-k (spec §Phase 5 acceptance)."""
    idx = FaissIndex.create(dimension=16)
    np.random.seed(42)
    vecs = np.random.rand(50, 16).astype(np.float32)
    vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
    ids = np.arange(50, dtype=np.int64)
    idx.add(vecs, ids)

    path = tmp_path / "index.faiss"
    idx.save(path)

    query = np.random.rand(16).astype(np.float32)
    query = query / np.linalg.norm(query)

    scores1, ids1 = idx.search(query, top_k=5)

    reloaded = FaissIndex.load(path)
    scores2, ids2 = reloaded.search(query, top_k=5)

    np.testing.assert_array_equal(ids1, ids2)
    np.testing.assert_allclose(scores1, scores2)


def test_contains() -> None:
    idx = FaissIndex.create(dimension=4)
    vecs = np.array([[1, 0, 0, 0]], dtype=np.float32)
    ids = np.array([42], dtype=np.int64)
    idx.add(vecs, ids)
    assert idx.contains(42)
    assert not idx.contains(99)

from __future__ import annotations

import numpy as np
import pytest

from app.embedding.base import ModelManifest, ZeroVectorError, l2_normalize


def test_model_manifest_signature_stable() -> None:
    m1 = ModelManifest(model_id="a", revision="1", dimension=768)
    m2 = ModelManifest(model_id="a", revision="1", dimension=768)
    assert m1.signature == m2.signature


def test_model_manifest_signature_changes_on_dim() -> None:
    m1 = ModelManifest(model_id="a", revision="1", dimension=768)
    m2 = ModelManifest(model_id="a", revision="1", dimension=1024)
    assert m1.signature != m2.signature


def test_model_manifest_signature_changes_on_prefix() -> None:
    m1 = ModelManifest(model_id="a", revision="1", dimension=768, query_prefix="query: ")
    m2 = ModelManifest(model_id="a", revision="1", dimension=768, query_prefix="")
    assert m1.signature != m2.signature


def test_model_manifest_to_dict_contains_all_fields() -> None:
    m = ModelManifest(model_id="a", revision="1", dimension=768)
    d = m.to_dict()
    assert "model_id" in d
    assert "revision" in d
    assert "dimension" in d
    assert "normalize" in d
    assert "query_prefix" in d
    assert "passage_prefix" in d
    assert "pooling" in d
    assert "signature" in d


def test_l2_normalize_basic() -> None:
    v = np.array([[3.0, 4.0]], dtype=np.float32)
    normed = l2_normalize(v)
    assert np.allclose(np.linalg.norm(normed, axis=1), 1.0)


def test_l2_normalize_multi_row() -> None:
    v = np.array([[3.0, 4.0], [1.0, 0.0]], dtype=np.float32)
    normed = l2_normalize(v)
    assert np.allclose(np.linalg.norm(normed, axis=1), [1.0, 1.0])


def test_l2_normalize_zero_vector_rejected() -> None:
    v = np.array([[0.0, 0.0]], dtype=np.float32)
    with pytest.raises(ZeroVectorError):
        l2_normalize(v)


def test_l2_normalize_partial_zero() -> None:
    v = np.array([[1.0, 0.0], [0.0, 0.0]], dtype=np.float32)
    with pytest.raises(ZeroVectorError):
        l2_normalize(v)


def test_l2_normalize_preserves_float32() -> None:
    v = np.array([[3.0, 4.0]], dtype=np.float32)
    normed = l2_normalize(v)
    assert normed.dtype == np.float32

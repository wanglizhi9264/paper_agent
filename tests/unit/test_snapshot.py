from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.embedding.base import ModelManifest
from app.index.faiss_index import FaissIndex
from app.index.snapshot import (
    ManifestValidationError,
    SnapshotManifest,
    build_manifest,
    load_manifest,
    save_manifest,
    validate_manifest,
)


def _make_manifest(dimension: int = 8) -> ModelManifest:
    return ModelManifest(
        model_id="test-model",
        revision="v1",
        dimension=dimension,
        normalize=True,
        query_prefix="query: ",
        passage_prefix="passage: ",
        pooling="mean",
    )


def _make_faiss(tmp_path: Path, dim: int = 8, n: int = 5) -> Path:
    idx = FaissIndex.create(dim)
    vecs = np.random.rand(n, dim).astype(np.float32)
    vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
    ids = np.arange(n, dtype=np.int64)
    idx.add(vecs, ids)
    path = tmp_path / "index.faiss"
    idx.save(path)
    return path


def test_build_manifest(tmp_path) -> None:
    emb = _make_manifest()
    faiss_path = _make_faiss(tmp_path, dim=8, n=5)
    manifest = build_manifest(
        manifest_embedding=emb,
        faiss_path=faiss_path,
        document_versions={"doc-1": "ver-1"},
        document_count=1,
        chunk_count=5,
        max_faiss_id=4,
    )
    assert manifest.embedding["signature"] == emb.signature
    assert manifest.chunk_count == 5
    assert manifest.max_faiss_id == 4
    assert manifest.document_count == 1
    assert manifest.document_versions == {"doc-1": "ver-1"}
    assert manifest.faiss_sha256  # non-empty


def test_manifest_sha256_stable(tmp_path) -> None:
    emb = _make_manifest()
    faiss_path = _make_faiss(tmp_path, dim=8, n=3)
    m1 = build_manifest(
        manifest_embedding=emb,
        faiss_path=faiss_path,
        document_versions={"d1": "v1"},
        document_count=1,
        chunk_count=3,
        max_faiss_id=2,
    )
    m2 = build_manifest(
        manifest_embedding=emb,
        faiss_path=faiss_path,
        document_versions={"d1": "v1"},
        document_count=1,
        chunk_count=3,
        max_faiss_id=2,
    )
    assert m1.sha256 == m2.sha256


def test_manifest_serialization(tmp_path) -> None:
    emb = _make_manifest()
    manifest = SnapshotManifest(
        schema_version=1,
        embedding=emb.to_dict(),
        document_versions={"d1": "v1"},
        document_count=1,
        chunk_count=3,
        max_faiss_id=2,
        faiss_file="index.faiss",
    )
    j = manifest.to_json()
    restored = SnapshotManifest.from_json(j)
    assert restored == manifest


def test_save_and_load_manifest(tmp_path) -> None:
    emb = _make_manifest()
    faiss_path = _make_faiss(tmp_path)
    manifest = build_manifest(
        manifest_embedding=emb,
        faiss_path=faiss_path,
        document_versions={"d1": "v1"},
        document_count=1,
        chunk_count=5,
        max_faiss_id=4,
    )
    path = tmp_path / "manifest.json"
    save_manifest(manifest, path)
    assert path.exists()
    loaded = load_manifest(path)
    assert loaded.sha256 == manifest.sha256


def test_validate_manifest_ok(tmp_path) -> None:
    emb = _make_manifest()
    faiss_path = _make_faiss(tmp_path, dim=8, n=5)
    manifest = build_manifest(
        manifest_embedding=emb,
        faiss_path=faiss_path,
        document_versions={"d1": "v1"},
        document_count=1,
        chunk_count=5,
        max_faiss_id=4,
    )
    # Should not raise
    validate_manifest(
        manifest,
        faiss_path=faiss_path,
        expected_embedding_signature=emb.signature,
        expected_dimension=8,
        db_document_versions={"d1": "v1"},
    )


def test_validate_manifest_hash_mismatch(tmp_path) -> None:
    emb = _make_manifest()
    faiss_path = _make_faiss(tmp_path, dim=8, n=5)
    manifest = build_manifest(
        manifest_embedding=emb,
        faiss_path=faiss_path,
        document_versions={"d1": "v1"},
        document_count=1,
        chunk_count=5,
        max_faiss_id=4,
    )
    # Tamper with the file
    with faiss_path.open("a") as f:
        f.write("garbage")
    with pytest.raises(ManifestValidationError, match="hash mismatch"):
        validate_manifest(manifest, faiss_path=faiss_path)


def test_validate_manifest_file_missing(tmp_path) -> None:
    emb = _make_manifest()
    manifest = SnapshotManifest(
        embedding=emb.to_dict(),
        chunk_count=5,
        faiss_sha256="x",
        faiss_file="nonexistent.faiss",
    )
    with pytest.raises(ManifestValidationError, match="faiss file missing"):
        validate_manifest(manifest, faiss_path=tmp_path / "nonexistent.faiss")


def test_validate_manifest_chunk_count_mismatch(tmp_path) -> None:
    emb = _make_manifest()
    faiss_path = _make_faiss(tmp_path, dim=8, n=5)
    manifest = build_manifest(
        manifest_embedding=emb,
        faiss_path=faiss_path,
        document_versions={"d1": "v1"},
        document_count=1,
        chunk_count=10,  # wrong!
        max_faiss_id=4,
    )
    with pytest.raises(ManifestValidationError, match="chunk_count"):
        validate_manifest(manifest, faiss_path=faiss_path)


def test_validate_manifest_signature_mismatch(tmp_path) -> None:
    emb = _make_manifest()
    faiss_path = _make_faiss(tmp_path, dim=8, n=5)
    manifest = build_manifest(
        manifest_embedding=emb,
        faiss_path=faiss_path,
        document_versions={"d1": "v1"},
        document_count=1,
        chunk_count=5,
        max_faiss_id=4,
    )
    with pytest.raises(ManifestValidationError, match="signature mismatch"):
        validate_manifest(
            manifest,
            faiss_path=faiss_path,
            expected_embedding_signature="wrong_sig",
        )


def test_validate_manifest_dimension_mismatch(tmp_path) -> None:
    emb = _make_manifest(dimension=8)
    faiss_path = _make_faiss(tmp_path, dim=8, n=5)
    manifest = build_manifest(
        manifest_embedding=emb,
        faiss_path=faiss_path,
        document_versions={"d1": "v1"},
        document_count=1,
        chunk_count=5,
        max_faiss_id=4,
    )
    with pytest.raises(ManifestValidationError, match="dimension"):
        validate_manifest(
            manifest,
            faiss_path=faiss_path,
            expected_dimension=16,
        )


def test_validate_manifest_db_version_mismatch(tmp_path) -> None:
    emb = _make_manifest()
    faiss_path = _make_faiss(tmp_path, dim=8, n=5)
    manifest = build_manifest(
        manifest_embedding=emb,
        faiss_path=faiss_path,
        document_versions={"d1": "v1"},
        document_count=1,
        chunk_count=5,
        max_faiss_id=4,
    )
    with pytest.raises(ManifestValidationError, match="version mismatch"):
        validate_manifest(
            manifest,
            faiss_path=faiss_path,
            db_document_versions={"d1": "different_version"},
        )


def test_validate_manifest_db_versions_match(tmp_path) -> None:
    emb = _make_manifest()
    faiss_path = _make_faiss(tmp_path, dim=8, n=5)
    manifest = build_manifest(
        manifest_embedding=emb,
        faiss_path=faiss_path,
        document_versions={"d1": "v1", "d2": "v2"},
        document_count=2,
        chunk_count=5,
        max_faiss_id=4,
    )
    validate_manifest(
        manifest,
        faiss_path=faiss_path,
        db_document_versions={"d1": "v1", "d2": "v2"},
    )

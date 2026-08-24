from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.embedding.base import ModelManifest
from app.index.faiss_index import FaissIndex


@dataclass(frozen=True)
class SnapshotManifest:
    """Manifest stored alongside a FAISS/BM25 snapshot (spec §9.5, §13.3).

    Validates that the FAISS index, the embedding signature, and the
    database's DocumentVersion mappings are all mutually consistent.
    """

    schema_version: int = 1
    embedding: dict[str, Any] = field(default_factory=dict)
    analyzer: dict[str, Any] | None = None
    document_versions: dict[str, str] = field(default_factory=dict)
    document_count: int = 0
    chunk_count: int = 0
    max_faiss_id: int = 0
    faiss_file: str = ""
    bm25_file: str = ""
    faiss_sha256: str = ""
    created_at: str = ""

    @property
    def sha256(self) -> str:
        d = self.to_dict()
        d.pop("created_at", None)
        raw = json.dumps(d, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SnapshotManifest:
        return cls(
            schema_version=d.get("schema_version", 1),
            embedding=d.get("embedding", {}),
            analyzer=d.get("analyzer"),
            document_versions=d.get("document_versions", {}),
            document_count=d.get("document_count", 0),
            chunk_count=d.get("chunk_count", 0),
            max_faiss_id=d.get("max_faiss_id", 0),
            faiss_file=d.get("faiss_file", ""),
            bm25_file=d.get("bm25_file", ""),
            faiss_sha256=d.get("faiss_sha256", ""),
            created_at=d.get("created_at", ""),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> SnapshotManifest:
        return cls.from_dict(json.loads(s))


class ManifestValidationError(Exception):
    """Raised when manifest validation fails (spec §13.3)."""

    code = "MANIFEST_INVALID"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(
    *,
    manifest_embedding: ModelManifest,
    faiss_path: Path,
    document_versions: dict[str, str],
    document_count: int,
    chunk_count: int,
    max_faiss_id: int,
    analyzer: dict[str, Any] | None = None,
    bm25_path: Path | None = None,
    schema_version: int = 1,
) -> SnapshotManifest:
    faiss_sha = file_sha256(faiss_path)
    return SnapshotManifest(
        schema_version=schema_version,
        embedding=manifest_embedding.to_dict(),
        analyzer=analyzer,
        document_versions=document_versions,
        document_count=document_count,
        chunk_count=chunk_count,
        max_faiss_id=max_faiss_id,
        faiss_file=faiss_path.name,
        bm25_file=bm25_path.name if bm25_path else "",
        faiss_sha256=faiss_sha,
        created_at=datetime.now(UTC).isoformat(),
    )


def validate_manifest(
    manifest: SnapshotManifest,
    *,
    faiss_path: Path,
    expected_embedding_signature: str | None = None,
    expected_dimension: int | None = None,
    db_document_versions: dict[str, str] | None = None,
) -> None:
    """Validate manifest against the on-disk FAISS file and DB state (spec §13.3).

    Raises ManifestValidationError on any inconsistency.
    """
    if not faiss_path.exists():
        raise ManifestValidationError(
            f"faiss file missing: {faiss_path.name}",
            code="FAISS_FILE_MISSING",
        )
    actual_sha = file_sha256(faiss_path)
    if actual_sha != manifest.faiss_sha256:
        raise ManifestValidationError(
            f"faiss file hash mismatch: expected={manifest.faiss_sha256[:16]}, "
            f"actual={actual_sha[:16]}",
            code="FAISS_HASH_MISMATCH",
        )
    faiss_index = FaissIndex.load(faiss_path)
    if faiss_index.ntotal != manifest.chunk_count:
        raise ManifestValidationError(
            f"faiss ntotal={faiss_index.ntotal} != manifest chunk_count={manifest.chunk_count}",
            code="CHUNK_COUNT_MISMATCH",
        )
    emb_sig = manifest.embedding.get("signature", "")
    if expected_embedding_signature and emb_sig != expected_embedding_signature:
        raise ManifestValidationError(
            f"embedding signature mismatch: expected={expected_embedding_signature}, got={emb_sig}",
            code="SIGNATURE_MISMATCH",
        )
    emb_dim = manifest.embedding.get("dimension", 0)
    if expected_dimension and emb_dim != expected_dimension:
        raise ManifestValidationError(
            f"dimension mismatch: expected={expected_dimension}, got={emb_dim}",
            code="DIMENSION_MISMATCH",
        )
    if faiss_index.dimension != emb_dim:
        raise ManifestValidationError(
            f"faiss dim={faiss_index.dimension} != manifest dim={emb_dim}",
            code="DIMENSION_MISMATCH",
        )
    if db_document_versions is not None:
        for doc_id, ver_id in manifest.document_versions.items():
            actual = db_document_versions.get(doc_id)
            if actual != ver_id:
                raise ManifestValidationError(
                    f"document {doc_id} version mismatch: manifest={ver_id}, db={actual}",
                    code="VERSION_MISMATCH",
                )


def save_manifest(manifest: SnapshotManifest, path: Path) -> None:
    """Write manifest JSON to *path* via temp + atomic rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(manifest.to_json(), encoding="utf-8")
    tmp.replace(path)


def load_manifest(path: Path) -> SnapshotManifest:
    return SnapshotManifest.from_json(path.read_text(encoding="utf-8"))


def atomic_activate_snapshot(
    *,
    building_dir: Path,
    active_dir: Path,
    faiss_filename: str,
    manifest_filename: str,
    bm25_filename: str | None = None,
) -> Path:
    """Move a validated shadow snapshot from building_dir to active_dir.

    Uses atomic rename. Returns the active directory path.
    """
    active_dir.mkdir(parents=True, exist_ok=True)
    tmp_active = active_dir.with_suffix(".activating")
    if tmp_active.exists():
        import shutil

        shutil.rmtree(tmp_active)
    tmp_active.mkdir(parents=True)

    for fname in [faiss_filename, manifest_filename, bm25_filename]:
        if fname is None:
            continue
        src = building_dir / fname
        dst = tmp_active / fname
        if not src.exists():
            continue
        src.replace(dst)

    tmp_active.replace(active_dir)
    return active_dir

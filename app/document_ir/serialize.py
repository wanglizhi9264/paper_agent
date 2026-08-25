"""Canonical JSON serialization and stable hashing (spec §5.1, §6.2).

Serialization is UTF-8, ``sort_keys=True``, compact separators — the same
input always produces the same bytes so SHA-256 fingerprints are stable.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.document_ir.models import DocumentIR, IRModel, ParserManifest
from app.document_ir.normalize import NORMALIZER_VERSION

IR_SCHEMA_VERSION = 2


def canonical_json(model: IRModel) -> str:
    """Serialize *model* to canonical JSON (sorted keys, compact)."""
    data = model.model_dump(mode="json")
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ir_sha256(ir: DocumentIR) -> str:
    """Full SHA-256 hex digest of the canonical JSON for *ir*."""
    return hashlib.sha256(canonical_json(ir).encode("utf-8")).hexdigest()


def compute_parser_signature(
    *,
    parser_id: str,
    parser_version: str,
    model_ids: dict[str, str] | None = None,
    model_revisions: dict[str, str] | None = None,
    options: dict[str, bool | int | float | str] | None = None,
) -> str:
    """Parser signature per spec §6.2.

    SHA-256 over canonical JSON of parser identity, model ids/revisions,
    options, the fixed IR schema version, and the normalizer version. Runtime
    timing and absolute paths are never part of the payload.
    """
    payload = {
        "parser_id": parser_id,
        "parser_version": parser_version,
        "model_ids": model_ids or {},
        "model_revisions": model_revisions or {},
        "options": options or {},
        "ir_schema_version": IR_SCHEMA_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def manifest_signature(manifest: ParserManifest) -> str:
    """Recompute a signature from an existing :class:`ParserManifest`."""
    return compute_parser_signature(
        parser_id=manifest.parser_id,
        parser_version=manifest.parser_version,
        model_ids=dict(manifest.model_ids),
        model_revisions=dict(manifest.model_revisions),
        options=dict(manifest.options),
    )


def write_ir(ir: DocumentIR, path: Path) -> None:
    """Write canonical JSON artifact, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(ir), encoding="utf-8")


def read_ir(path: Path) -> DocumentIR:
    """Load a :class:`DocumentIR` from canonical JSON."""
    return DocumentIR.model_validate_json(path.read_text(encoding="utf-8"))

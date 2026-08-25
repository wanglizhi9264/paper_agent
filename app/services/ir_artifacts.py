"""Canonical IR artifact staging, validation, activation, and recovery."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from app.document_ir.errors import IR_ARTIFACT_INVALID, ParseError
from app.document_ir.models import DocumentIR
from app.document_ir.serialize import canonical_json, ir_sha256, read_ir
from app.document_ir.validate import validate_document_ir


@dataclass(frozen=True, slots=True)
class IRArtifactRecord:
    ir_relative_path: str
    ir_sha256: str
    parser_signature: str


def _render_markdown(ir: DocumentIR) -> str:
    blocks: list[str] = []
    for element in sorted(ir.elements, key=lambda value: value.reading_order):
        if element.kind == "table" and element.table is not None:
            blocks.append(element.table.markdown)
        elif element.raw_text.strip():
            blocks.append(element.raw_text.strip())
    return "\n\n".join(blocks) + "\n"


class IRArtifactManager:
    """Manage immutable IR artifacts below one configured storage root."""

    def __init__(self, storage_dir: Path) -> None:
        self.storage_dir = Path(storage_dir).resolve()

    def _safe(self, relative: str | Path) -> Path:
        raw = Path(relative)
        if raw.is_absolute():
            raise ParseError("IR artifact path must be relative", code=IR_ARTIFACT_INVALID)
        resolved = (self.storage_dir / raw).resolve()
        if not resolved.is_relative_to(self.storage_dir):
            raise ParseError("IR artifact path escapes storage", code=IR_ARTIFACT_INVALID)
        return resolved

    def _record(self, ir_path: Path, digest: str, signature: str) -> IRArtifactRecord:
        return IRArtifactRecord(
            ir_relative_path=ir_path.relative_to(self.storage_dir).as_posix(),
            ir_sha256=digest,
            parser_signature=signature,
        )

    def stage(self, version_id: UUID, ir: DocumentIR) -> IRArtifactRecord:
        validation = validate_document_ir(ir)
        if validation.issues:
            raise ParseError("Canonical IR validation failed", code=IR_ARTIFACT_INVALID)
        version_root = self._safe(Path("ir") / "building" / str(version_id))
        target = version_root / ir.parser.signature
        if target.exists():
            raise ParseError("IR artifact candidate already exists", code=IR_ARTIFACT_INVALID)
        version_root.mkdir(parents=True, exist_ok=True)
        temporary = version_root / f".{ir.parser.signature}.tmp-{uuid4().hex}"
        temporary.mkdir()
        try:
            ir_path = temporary / "document_ir.json"
            ir_path.write_text(canonical_json(ir), encoding="utf-8")
            (temporary / "document.md").write_text(_render_markdown(ir), encoding="utf-8")
            (temporary / "quality.json").write_text(
                json.dumps(ir.quality.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            digest = ir_sha256(ir)
            self._verify_absolute(ir_path, digest)
            temporary.replace(target)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        return self._record(target / "document_ir.json", digest, ir.parser.signature)

    def _verify_absolute(self, path: Path, expected_sha256: str) -> DocumentIR:
        try:
            raw = path.read_bytes()
            actual = hashlib.sha256(raw).hexdigest()
            ir = read_ir(path)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ParseError("IR artifact cannot be read", code=IR_ARTIFACT_INVALID) from exc
        if actual != expected_sha256 or ir_sha256(ir) != expected_sha256:
            raise ParseError("IR artifact hash mismatch", code=IR_ARTIFACT_INVALID)
        validation = validate_document_ir(ir)
        if validation.issues:
            raise ParseError("IR artifact schema/quality invalid", code=IR_ARTIFACT_INVALID)
        return ir

    def verify(self, relative_path: str, expected_sha256: str) -> DocumentIR:
        return self._verify_absolute(self._safe(relative_path), expected_sha256)

    def activate(self, version_id: UUID, parser_signature: str) -> IRArtifactRecord:
        source = self._safe(Path("ir") / "building" / str(version_id))
        target = self._safe(Path("ir") / "versions" / str(version_id))
        if target.exists():
            raise ParseError("active IR artifact already exists", code=IR_ARTIFACT_INVALID)
        candidate = source / parser_signature / "document_ir.json"
        if not candidate.is_file():
            raise ParseError("staged IR artifact is missing", code=IR_ARTIFACT_INVALID)
        ir = read_ir(candidate)
        digest = ir_sha256(ir)
        self._verify_absolute(candidate, digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
        activated_path = target / parser_signature / "document_ir.json"
        return self._record(activated_path, digest, parser_signature)

    def fail(self, version_id: UUID, job_id: UUID) -> Path | None:
        source = self._safe(Path("ir") / "building" / str(version_id))
        if not source.exists():
            source = self._safe(Path("ir") / "versions" / str(version_id))
            if not source.exists():
                return None
        target = self._safe(Path("tmp") / "failed" / str(job_id) / str(version_id))
        if target.exists():
            raise ParseError("failed artifact target already exists", code=IR_ARTIFACT_INVALID)
        target.parent.mkdir(parents=True, exist_ok=True)
        source.replace(target)
        return target

    def remove_version(self, version_id: UUID) -> None:
        for relative in (
            Path("ir") / "building" / str(version_id),
            Path("ir") / "versions" / str(version_id),
        ):
            target = self._safe(relative)
            if target.exists():
                shutil.rmtree(target)

    def quarantine_orphans(self, known_version_ids: set[UUID]) -> int:
        """Move artifact directories with no DB version to the failed area."""
        known = {str(value) for value in known_version_ids}
        count = 0
        destination_root = self._safe(Path("tmp") / "failed" / "orphans")
        for area in ("building", "versions"):
            root = self._safe(Path("ir") / area)
            if not root.is_dir():
                continue
            for candidate in sorted(root.iterdir()):
                if not candidate.is_dir() or candidate.name in known:
                    continue
                destination = destination_root / f"{area}-{candidate.name}"
                if destination.exists():
                    raise ParseError(
                        "orphan quarantine target already exists", code=IR_ARTIFACT_INVALID
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                candidate.replace(destination)
                count += 1
        return count


__all__ = ["IRArtifactManager", "IRArtifactRecord"]

"""Parser protocol and parse candidate result (spec §6.1).

Parsing runs only inside the ARQ worker; FastAPI routes never invoke parsers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import Field

from app.document_ir.models import DocumentIR, IRModel, ParserManifest


@runtime_checkable
class DocumentParser(Protocol):
    """Every V2 adapter implements this protocol (spec §6.1)."""

    @property
    def manifest(self) -> ParserManifest: ...

    def parse(self, path: Path, *, document_id: UUID) -> DocumentIR: ...


class ParseCandidate(IRModel):
    """One complete parser output for a PDF (spec §3.4, §6.1).

    A DocumentVersion activates at most one candidate; per-page mixing of
    candidates is forbidden in the first version.
    """

    parser_id: str
    document_ir: DocumentIR
    artifact_path: str
    artifact_sha256: str
    elapsed_ms: int = Field(ge=0)

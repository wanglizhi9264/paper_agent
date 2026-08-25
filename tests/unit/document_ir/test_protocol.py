"""Tests for parser protocol and ParseCandidate (spec §6.1)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.document_ir.models import DocumentIR, ParserManifest
from app.document_ir.protocol import DocumentParser, ParseCandidate
from app.document_ir.serialize import canonical_json, ir_sha256
from tests.unit.document_ir.builders import make_element, make_ir, make_manifest


class _FakeParser:
    def __init__(self) -> None:
        self._manifest: ParserManifest = make_manifest()

    @property
    def manifest(self) -> ParserManifest:
        return self._manifest

    def parse(self, path: Path, *, document_id: UUID) -> DocumentIR:
        return make_ir(elements=[make_element()])


class TestProtocol:
    def test_fake_satisfies_protocol(self) -> None:
        parser = _FakeParser()
        assert isinstance(parser, DocumentParser)

    def test_non_parser_rejected(self) -> None:
        assert not isinstance(object(), DocumentParser)

    def test_parse_returns_ir(self, tmp_path: Path) -> None:
        parser = _FakeParser()
        ir = parser.parse(tmp_path / "x.pdf", document_id=uuid4())
        assert ir.schema_version == 2


class TestParseCandidate:
    def test_valid_candidate(self) -> None:
        ir = make_ir(elements=[make_element()])
        candidate = ParseCandidate(
            parser_id="pymupdf",
            document_ir=ir,
            artifact_path="storage/ir/building/x/document_ir.json",
            artifact_sha256=ir_sha256(ir),
            elapsed_ms=120,
        )
        assert candidate.elapsed_ms == 120

    def test_negative_elapsed_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ParseCandidate(
                parser_id="pymupdf",
                document_ir=make_ir(),
                artifact_path="p",
                artifact_sha256="a",
                elapsed_ms=-1,
            )

    def test_extra_field_rejected(self) -> None:
        payload = make_ir().model_dump(mode="json")
        candidate_payload = {
            "parser_id": "pymupdf",
            "document_ir": payload,
            "artifact_path": "p",
            "artifact_sha256": hashlib.sha256(canonical_json(make_ir()).encode()).hexdigest(),
            "elapsed_ms": 5,
            "surprise": True,
        }
        with pytest.raises(ValidationError):
            ParseCandidate.model_validate(candidate_payload)

    def test_round_trip(self) -> None:
        ir = make_ir(elements=[make_element()])
        candidate = ParseCandidate(
            parser_id="docling",
            document_ir=ir,
            artifact_path="storage/ir/versions/v/document_ir.json",
            artifact_sha256=ir_sha256(ir),
            elapsed_ms=42,
        )
        restored = ParseCandidate.model_validate_json(candidate.model_dump_json())
        assert restored == candidate

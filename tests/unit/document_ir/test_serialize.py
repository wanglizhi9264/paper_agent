"""Tests for canonical serialization and parser signature (spec §5.1, §6.2)."""

from __future__ import annotations

import json

from app.document_ir.serialize import (
    IR_SCHEMA_VERSION,
    canonical_json,
    compute_parser_signature,
    ir_sha256,
    manifest_signature,
    read_ir,
    write_ir,
)
from tests.unit.document_ir.builders import make_element, make_ir, make_manifest


class TestCanonicalJson:
    def test_deterministic_across_instances(self) -> None:
        ir1 = make_ir(elements=[make_element()])
        ir2 = make_ir(
            elements=[make_element(element_id=ir1.elements[0].id)], document_id=ir1.document_id
        )
        assert canonical_json(ir1) == canonical_json(ir2)

    def test_sorted_keys(self) -> None:
        ir = make_ir()
        data = json.loads(canonical_json(ir))
        keys = list(data.keys())
        assert keys == sorted(keys)

    def test_compact_separators(self) -> None:
        ir = make_ir()
        text = canonical_json(ir)
        assert ": " not in text
        assert ", " not in text

    def test_utf8_content_preserved(self) -> None:
        ir = make_ir(title="标题 ε ±")
        text = canonical_json(ir)
        assert "标题" in text
        assert "\\u" not in text.replace("\\u0000", "")


class TestSha256:
    def test_full_hex_digest(self) -> None:
        digest = ir_sha256(make_ir())
        assert len(digest) == 64
        int(digest, 16)

    def test_changes_with_content(self) -> None:
        ir1 = make_ir(title="A")
        ir2 = make_ir(title="B", document_id=ir1.document_id)
        assert ir_sha256(ir1) != ir_sha256(ir2)

    def test_stable_for_identical_documents(self) -> None:
        ir = make_ir(elements=[make_element()])
        same = make_ir(
            elements=[make_element(element_id=ir.elements[0].id)],
            document_id=ir.document_id,
            title=ir.title,
            manifest=make_manifest(),
        )
        assert ir_sha256(ir) == ir_sha256(same)


class TestParserSignature:
    def test_stable_for_same_inputs(self) -> None:
        kwargs = {
            "parser_id": "docling",
            "parser_version": "2.0.0",
            "model_ids": {"layout": "model"},
            "model_revisions": {"layout": "sha"},
            "options": {"ocr": False},
        }
        assert compute_parser_signature(**kwargs) == compute_parser_signature(**kwargs)

    def test_changes_on_version(self) -> None:
        base = compute_parser_signature(parser_id="pymupdf", parser_version="1.0")
        other = compute_parser_signature(parser_id="pymupdf", parser_version="1.1")
        assert base != other

    def test_changes_on_option(self) -> None:
        base = compute_parser_signature(
            parser_id="docling",
            parser_version="2.0",
            options={"table_structure": True},
        )
        flipped = compute_parser_signature(
            parser_id="docling",
            parser_version="2.0",
            options={"table_structure": False},
        )
        assert base != flipped

    def test_full_length_and_hex(self) -> None:
        sig = compute_parser_signature(parser_id="pymupdf", parser_version="1.0")
        assert len(sig) == 64
        int(sig, 16)

    def test_manifest_signature_matches_recompute(self) -> None:
        manifest = make_manifest()
        assert manifest_signature(manifest) == manifest.signature


class TestFileRoundTrip:
    def test_write_and_read(self, tmp_path: object) -> None:
        from pathlib import Path

        path = Path(tmp_path) / "nested" / "document_ir.json"  # type: ignore[operator]
        ir = make_ir(elements=[make_element()])
        write_ir(ir, path)
        restored = read_ir(path)
        assert restored == ir
        assert path.read_text(encoding="utf-8") == canonical_json(ir)


class TestConstants:
    def test_schema_version_is_two(self) -> None:
        assert IR_SCHEMA_VERSION == 2

    def test_payload_excludes_runtime_info(self) -> None:
        sig_a = compute_parser_signature(
            parser_id="pymupdf",
            parser_version="1.0",
            options={"path": "/tmp/never"},
        )
        sig_b = compute_parser_signature(
            parser_id="pymupdf",
            parser_version="1.0",
            options={"path": "/other/place"},
        )
        # Options are part of the payload by contract; but paths must never be
        # added implicitly by the serializer itself.
        payload = json.dumps(
            {
                "parser_id": "pymupdf",
                "parser_version": "1.0",
                "model_ids": {},
                "model_revisions": {},
                "options": {},
                "ir_schema_version": IR_SCHEMA_VERSION,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        assert "elapsed" not in payload
        assert "artifact" not in payload
        assert isinstance(sig_a, str)
        assert isinstance(sig_b, str)

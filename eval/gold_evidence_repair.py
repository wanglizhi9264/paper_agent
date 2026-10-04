"""Resolve a stable private benchmark against the active parser/chunker output.

This command reads the database and original local PDFs, but never mutates the
database, source benchmark, questions, answers, or evidence quotes.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, no_type_check

import pymupdf
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import session_scope
from app.document_ir.serialize import read_ir
from app.models.chunk import Chunk, DocumentVersion
from app.models.document import Document
from app.models.index_snapshot import SystemState
from eval.gold_evidence import EvidenceChunk, repair_benchmark


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: expected a JSON object")
    return value


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _review_decisions(path: Path | None) -> dict[tuple[str, int], dict[str, Any]]:
    if path is None:
        return {}
    payload = _load(path)
    if payload.get("schema_version") != "gold-review-decisions-v1":
        raise ValueError("review decisions must use gold-review-decisions-v1")
    decisions: dict[tuple[str, int], dict[str, Any]] = {}
    for value in payload.get("decisions", []):
        if not isinstance(value, dict):
            raise ValueError("each review decision must be an object")
        key = (str(value["question_id"]), int(value["evidence_index"]))
        if key in decisions:
            raise ValueError(f"duplicate review decision: {key}")
        decisions[key] = value
    return decisions


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@no_type_check
def _source_pages(path: Path) -> dict[int, str]:
    """Read source pages through PyMuPDF, whose installed build lacks type hints."""
    document = pymupdf.open(path)
    try:
        return {index + 1: page.get_text() for index, page in enumerate(document)}
    finally:
        document.close()


def _parsed_pages(path: Path) -> dict[int, str]:
    ir = read_ir(path)
    pages: dict[int, list[str]] = {}
    for element in sorted(ir.elements, key=lambda value: value.reading_order):
        physical_pages = sorted({span.physical_page for span in element.provenance})
        for page in physical_pages:
            pages.setdefault(page, []).append(element.normalized_text)
    return {page: " ".join(values) for page, values in pages.items()}


def _as_evidence_chunk(document_key: str, chunk: Chunk) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=str(chunk.id),
        document_key=document_key,
        chunk_index=chunk.chunk_index,
        raw_content=chunk.raw_content,
        retrieval_content=chunk.retrieval_content,
        content_hash=chunk.content_hash,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        section_path=tuple(chunk.section_path),
        kind=str(chunk.kind),
        metadata=dict(chunk.metadata_ or {}),
    )


async def run(args: argparse.Namespace) -> dict[str, Any]:
    payload = _load(args.dataset)
    prior = _load(args.previous_resolved) if args.previous_resolved else None
    review_decisions = _review_decisions(args.review_decisions)
    documents = {str(item["document_key"]): item for item in payload.get("documents", [])}
    settings = get_settings()
    upload_by_sha = {
        _sha256(path): path for path in settings.uploads_dir.glob("*.pdf") if path.is_file()
    }

    chunks_by_document: dict[str, Sequence[EvidenceChunk]] = {}
    parsed_pages: dict[tuple[str, int], str] = {}
    source_pages: dict[tuple[str, int], str] = {}
    runtime_document_ids: dict[str, str] = {}

    async with session_scope() as session:
        state = (await session.execute(select(SystemState).where(SystemState.id == 1))).scalar_one()
        if state.active_index_snapshot_id is None:
            raise RuntimeError("No active IndexSnapshot")
        snapshot_id = str(state.active_index_snapshot_id)
        for document_key, metadata in documents.items():
            rows = (
                (
                    await session.execute(
                        select(Document)
                        .where(Document.sha256 == metadata["sha256"])
                        .order_by(Document.created_at.desc(), Document.id)
                    )
                )
                .scalars()
                .all()
            )
            active = [row for row in rows if row.active_document_version_id is not None]
            if not active:
                raise RuntimeError(f"{document_key}: active document not found")
            document = active[0]
            runtime_document_ids[document_key] = str(document.id)
            version = (
                await session.execute(
                    select(DocumentVersion).where(
                        DocumentVersion.id == document.active_document_version_id
                    )
                )
            ).scalar_one()
            chunks = (
                (
                    await session.execute(
                        select(Chunk)
                        .where(Chunk.document_version_id == version.id)
                        .order_by(Chunk.chunk_index, Chunk.id)
                    )
                )
                .scalars()
                .all()
            )
            chunks_by_document[document_key] = [
                _as_evidence_chunk(document_key, chunk) for chunk in chunks
            ]
            if version.ir_path:
                for page, text in _parsed_pages(settings.storage_dir / version.ir_path).items():
                    parsed_pages[(document_key, page)] = text
            source_path = upload_by_sha.get(str(metadata["sha256"]))
            if source_path is not None:
                for page, text in _source_pages(source_path).items():
                    source_pages[(document_key, page)] = text

    repaired, report = repair_benchmark(
        payload,
        chunks_by_document,
        snapshot_id=snapshot_id,
        parsed_pages=parsed_pages,
        source_pages=source_pages,
        prior_payload=prior,
        review_decisions=review_decisions,
    )
    for item in repaired.get("dataset", []):
        keys = item.get("scope", {}).get("document_keys", [])
        item["runtime_scope"] = {
            "type": "documents",
            "document_ids": [runtime_document_ids[str(key)] for key in keys],
        }

    resolved_items = [
        item
        for item in repaired.get("dataset", [])
        if not item.get("answerable")
        or item.get("evidence_resolution_status") in {"resolved", "multi_chunk"}
    ]
    unresolved_ids = {entry["id"] for entry in report["exceptions"]}
    unresolved_items = [
        item for item in repaired.get("dataset", []) if item.get("id") in unresolved_ids
    ]
    base = {key: value for key, value in repaired.items() if key != "dataset"}
    _write(args.output_dir / "benchmark_resolved.json", {**base, "dataset": resolved_items})
    _write(args.output_dir / "benchmark_unresolved.json", {**base, "dataset": unresolved_items})
    _write(args.output_dir / "benchmark_repaired.json", repaired)
    _write(args.output_dir / "benchmark_repair_report.json", report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--previous-resolved", type=Path)
    parser.add_argument("--review-decisions", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = asyncio.run(run(args))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["exceptions"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

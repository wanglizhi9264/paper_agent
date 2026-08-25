"""A/B comparison CLI for PDF parsers (spec pdf-ingestion-v2 §16).

Usage::

    uv run python -m app.cli.pdf_ab \
        --input <pdf-path> \
        --parsers pymupdf,docling \
        --pages 5,6,10 \
        --output eval/runs/pdf-v2/<run-id>

Writes ``manifest.json``, per-parser ``document_ir.json`` / ``document.md`` /
``quality.json``, and ``comparison.json`` + ``comparison.md``. The CLI never
writes the database, never activates a DocumentVersion, and never touches
benchmark data. MinerU is rejected until V2-4.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib
import json
import platform
import sys
import time
import uuid
from pathlib import Path
from typing import Any, ClassVar, Protocol, cast

from app.document_ir.errors import ParseError
from app.document_ir.models import DocumentIR
from app.document_ir.serialize import write_ir
from app.document_ir.validate import validate_document_ir

# --- Process memory measurement (no extra dependencies) --------------------


def _peak_rss_bytes_windows() -> int | None:
    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_: ClassVar[list[tuple[str, Any]]] = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    class _INFO(ctypes.Structure):
        _fields_: ClassVar[list[tuple[str, Any]]] = [
            ("cb", ctypes.c_ulong),
            ("Counters", PROCESS_MEMORY_COUNTERS),
        ]

    psapi = ctypes.WinDLL("psapi.dll")
    kernel32 = ctypes.WinDLL("kernel32.dll")
    info = _INFO()
    info.cb = ctypes.sizeof(_INFO())
    handle = kernel32.GetCurrentProcess()
    if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(info), info.cb):
        return None
    return int(info.Counters.PeakWorkingSetSize)


_IS_WINDOWS = sys.platform == "win32"


class _ResourceModule(Protocol):
    RUSAGE_SELF: int

    @staticmethod
    def getrusage(who: int) -> Any: ...


def peak_rss_bytes() -> int | None:
    """Peak resident set size of this process, platform-appropriate."""
    try:
        if _IS_WINDOWS:
            return _peak_rss_bytes_windows()
        resource_module = cast(_ResourceModule, importlib.import_module("resource"))
        ru_maxrss = resource_module.getrusage(resource_module.RUSAGE_SELF).ru_maxrss
        multiplier = 1024 if str(sys.platform).startswith("linux") else 1
        return int(ru_maxrss) * multiplier
    except Exception:  # pragma: no cover - defensive, diagnostics only
        return None


def reset_peak_vram() -> None:
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except (ImportError, OSError, RuntimeError):  # pragma: no cover - torch optional
        return


def peak_vram_bytes() -> int | None:
    try:
        import torch

        if torch.cuda.is_available():
            return int(torch.cuda.max_memory_allocated())
    except (ImportError, OSError, RuntimeError):  # pragma: no cover - torch optional
        return None
    return None


def render_document_markdown(ir: DocumentIR) -> str:
    """Deterministic plain-Markdown rendering used for A/B diffing."""
    lines: list[str] = []
    for element in sorted(ir.elements, key=lambda e: e.reading_order):
        if element.kind == "title":
            lines.append(f"# {element.raw_text.strip()}")
        elif element.kind == "heading":
            level = min(max(len(element.section_path) + 1, 2), 6)
            lines.append(f"{'#' * level} {element.raw_text.strip()}")
        elif element.kind == "table" and element.table is not None:
            lines.append(element.table.markdown.rstrip("\n"))
        else:
            text = element.raw_text.strip()
            if text:
                lines.append(text)
    return "\n\n".join(lines) + "\n"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_pages(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    pages = [int(part) for part in raw.split(",") if part.strip()]
    return pages or None


def _build_parser(name: str) -> Any:
    if name == "pymupdf":
        from app.loaders.pymupdf_adapter import PyMuPDFParser

        return PyMuPDFParser()
    if name == "docling":
        from app.core.config import get_settings
        from app.loaders.docling_adapter import DoclingParser

        return DoclingParser.from_settings(get_settings())
    if name == "mineru":
        from app.core.config import get_settings
        from app.loaders.mineru_adapter import MinerUParser

        settings = get_settings()
        if not settings.mineru_enabled:
            raise ParseError(
                "MinerU A/B requires PAPER_RAG_MINERU_ENABLED=true",
                code="PDF_PARSER_UNAVAILABLE",
            )
        return MinerUParser.from_settings(settings)
    raise ParseError(f"parser '{name}' is not available", code="PDF_PARSER_UNAVAILABLE")


def _challenger_conclusion(entries: dict[str, Any], anchors: list[str]) -> dict[str, Any] | None:
    """Summarize whether MinerU improves unresolved Docling evidence anchors."""
    if "mineru" not in entries:
        return None
    docling = entries.get("docling")
    mineru = entries["mineru"]
    if not anchors:
        return {"status": "pending", "reason": "NO_EVIDENCE_ANCHORS"}
    if not isinstance(docling, dict) or not docling.get("ok"):
        return {"status": "pending", "reason": "DOCLING_RESULT_UNAVAILABLE"}
    if not mineru.get("ok"):
        return {"status": "pending", "reason": "MINERU_RESULT_UNAVAILABLE"}
    docling_found = len(docling["anchors"]["found"])
    mineru_found = len(mineru["anchors"]["found"])
    if mineru_found > docling_found:
        status = "improved"
    elif mineru_found < docling_found:
        status = "regressed"
    else:
        status = "equivalent"
    return {
        "status": status,
        "reason": "EVIDENCE_ANCHOR_COUNT",
        "docling_found": docling_found,
        "mineru_found": mineru_found,
        "anchor_count": len(anchors),
    }


def _element_stats(ir: DocumentIR, pages_filter: list[int] | None) -> dict[str, Any]:
    scoped = [
        element
        for element in ir.elements
        if pages_filter is None
        or any(span.physical_page in pages_filter for span in element.provenance)
    ]
    total = len(scoped)
    with_page = sum(1 for e in scoped if e.provenance)
    with_bbox = sum(1 for e in scoped if any(s.bbox is not None for s in e.provenance))
    return {
        "elements_total": len(ir.elements),
        "elements_scoped": total,
        "page_coverage": round(with_page / total, 4) if total else None,
        "bbox_coverage": round(with_bbox / total, 4) if total else None,
    }


def _anchor_matches(
    ir: DocumentIR, anchors: list[str], pages_filter: list[int] | None
) -> dict[str, Any]:
    haystacks: list[str] = []
    for element in ir.elements:
        if pages_filter is not None and not any(
            span.physical_page in pages_filter for span in element.provenance
        ):
            continue
        haystacks.append(element.normalized_text)
        if element.table is not None:
            haystacks.extend(cell.normalized_text for cell in element.table.cells)
    found: list[str] = []
    missing: list[str] = []
    for anchor in anchors:
        target = anchor.strip()
        if any(target in hay for hay in haystacks):
            found.append(target)
        else:
            missing.append(target)
    return {
        "found": found,
        "missing": missing,
        "match_ratio": round(len(found) / len(anchors), 4) if anchors else None,
    }


def _run_one_parser(
    name: str,
    pdf_path: Path,
    document_id: Any,
    output_dir: Path,
    anchors: list[str],
    pages_filter: list[int] | None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"parser_id": name}
    try:
        parser = _build_parser(name)
        reset_peak_vram()
        started = time.perf_counter()
        ir = parser.parse(pdf_path, document_id=document_id)
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        parser_dir = output_dir / name
        write_ir(ir, parser_dir / "document_ir.json")
        (parser_dir / "document.md").write_text(render_document_markdown(ir), encoding="utf-8")
        (parser_dir / "quality.json").write_text(
            json.dumps(ir.quality.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        validation = validate_document_ir(ir)
        rss = peak_rss_bytes()
        vram = peak_vram_bytes()
        tables = [e.table for e in ir.elements if e.table is not None]
        entry.update(
            {
                "ok": True,
                "elapsed_ms": elapsed_ms,
                "peak_rss_mb": round(rss / (1024 * 1024), 1) if rss else None,
                "peak_vram_mb": round(vram / (1024 * 1024), 1) if vram else None,
                "table_count": ir.quality.table_count,
                "valid_table_count": sum(
                    1
                    for t in tables
                    if t.cells
                    and not any(c.row >= t.row_count or c.column >= t.column_count for c in t.cells)
                ),
                "invalid_table_count": ir.quality.malformed_table_count,
                "orphan_numeric_ratio": ir.quality.orphan_numeric_ratio,
                "replacement_character_count": ir.quality.replacement_character_count,
                "reading_order_confidence": ir.quality.reading_order_confidence,
                "hard_failures": list(ir.quality.hard_failures),
                "validator_ok": not validation.issues,
                "validator_issues": [
                    {"code": issue.code, "message": issue.message} for issue in validation.issues
                ],
                "pages": _element_stats(ir, pages_filter),
                "anchors": _anchor_matches(ir, anchors, pages_filter),
                "parser_manifest": ir.parser.model_dump(mode="json"),
            }
        )
    except ParseError as exc:
        entry.update({"ok": False, "error_code": exc.code, "error_message": str(exc)[:300]})
    except Exception as exc:
        entry.update(
            {
                "ok": False,
                "error_code": "PDF_PARSE_FAILED",
                "error_message": f"{type(exc).__name__}: {exc}"[:300],
            }
        )
    return entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A/B compare PDF V2 parsers (spec §16)")
    parser.add_argument("--input", required=True, help="PDF file to parse")
    parser.add_argument("--parsers", default="pymupdf,docling", help="comma-separated parsers")
    parser.add_argument("--pages", default=None, help="optional page filter, e.g. 5,6,10")
    parser.add_argument("--anchors", default="", help="optional comma-separated evidence anchors")
    parser.add_argument("--output", required=True, help="output directory (run id included)")
    args = parser.parse_args(argv)

    pdf_path = Path(args.input)
    if not pdf_path.exists():
        print(f"input PDF not found: {pdf_path.name}", file=sys.stderr)
        return 2
    names = [n.strip().lower() for n in args.parsers.split(",") if n.strip()]
    unknown = [n for n in names if n not in {"pymupdf", "docling", "mineru"}]
    if unknown:
        print(f"unknown parsers: {', '.join(unknown)}", file=sys.stderr)
        return 2

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    anchors = [a for a in args.anchors.split(",") if a.strip()]
    pages_filter = _parse_pages(args.pages)
    document_id = uuid.uuid4()

    manifest: dict[str, Any] = {
        "input_name": pdf_path.name,
        "input_sha256": _sha256_file(pdf_path),
        "input_size_bytes": pdf_path.stat().st_size,
        "parsers_requested": names,
        "pages_filter": pages_filter,
        "anchors": anchors,
        "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
    }

    entries: dict[str, Any] = {}
    for name in names:
        entries[name] = _run_one_parser(
            name, pdf_path, document_id, output_dir, anchors, pages_filter
        )

    challenger = _challenger_conclusion(entries, anchors)
    comparison = {**manifest, "results": entries, "mineru_challenger": challenger}
    (output_dir / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = ["# PDF Parser A/B Comparison", "", f"Input: `{manifest['input_name']}`", ""]
    header = "| parser | ok | elapsed_ms | tables | malformed | orphan | validator | anchors |"
    lines += [header, "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, entry in entries.items():
        if entry.get("ok"):
            anchors_cell = "-"
            ratio = entry["anchors"]["match_ratio"]
            if ratio is not None:
                anchors_cell = f"{len(entry['anchors']['found'])}/{len(anchors)}"
            lines.append(
                f"| {name} | yes | {entry['elapsed_ms']} | {entry['table_count']} "
                f"| {entry['invalid_table_count']} | {entry['orphan_numeric_ratio']:.3f} "
                f"| {'ok' if entry['validator_ok'] else 'FAIL'} | {anchors_cell} |"
            )
        else:
            lines.append(f"| {name} | no | - | - | - | - | - | {entry.get('error_code')} |")
    if challenger is not None:
        lines.extend(
            [
                "",
                "## MinerU challenger conclusion",
                "",
                f"Status: `{challenger['status']}` ({challenger['reason']}).",
            ]
        )
    (output_dir / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    succeeded = [e for e in entries.values() if e.get("ok")]
    print(f"A/B run written to {output_dir}")
    print(f"parsers ok: {len(succeeded)}/{len(entries)}")
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())

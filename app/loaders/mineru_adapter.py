"""Isolated MinerU challenger adapter for PDF Ingestion V2-4.

MinerU is never imported into the application environment.  The adapter invokes
an operator-managed CLI with an argv list, accepts output only below the current
job directory, converts the result to Canonical Document IR, and validates it.
"""

from __future__ import annotations

import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from uuid import UUID

from app.document_ir.errors import (
    PDF_LAYOUT_INVALID,
    PDF_PARSE_FAILED,
    PDF_PARSER_UNAVAILABLE,
    PDF_TABLE_INVALID,
    ParseError,
)
from app.document_ir.markdown import make_table_data
from app.document_ir.models import (
    BoundingBox,
    DocumentElement,
    DocumentIR,
    ElementKind,
    LayoutQualityReport,
    PageIR,
    ParserManifest,
    SourceSpan,
    TableCell,
)
from app.document_ir.normalize import normalize_for_retrieval
from app.document_ir.serialize import compute_parser_signature
from app.document_ir.validate import validate_document_ir
from app.loaders.pymupdf_adapter import count_orphan_numeric_cells


def build_mineru_argv(
    *, command: str, input_path: Path, output_dir: Path, backend: str
) -> list[str]:
    """Build the fixed shell-free MinerU CLI contract."""
    if not command or "\x00" in command:
        raise ParseError("MinerU command is not configured", code=PDF_PARSER_UNAVAILABLE)
    return [command, "-p", str(input_path), "-o", str(output_dir), "-b", backend]


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, bool, int, int]]] = []
        self._row: list[tuple[str, bool, int, int]] | None = None
        self._cell_parts: list[str] | None = None
        self._is_header = False
        self._rowspan = 1
        self._colspan = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "tr":
            self._row = []
        elif lowered in {"th", "td"} and self._row is not None:
            values = dict(attrs)
            self._cell_parts = []
            self._is_header = lowered == "th"
            self._rowspan = _positive_int(values.get("rowspan"), 1)
            self._colspan = _positive_int(values.get("colspan"), 1)

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"th", "td"} and self._row is not None and self._cell_parts is not None:
            self._row.append(
                ("".join(self._cell_parts).strip(), self._is_header, self._rowspan, self._colspan)
            )
            self._cell_parts = None
        elif lowered == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 1 else default


def _parse_markdown_table(text: str) -> list[list[tuple[str, bool, int, int]]]:
    rows: list[list[tuple[str, bool, int, int]]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        values = [part.strip() for part in stripped.strip("|").split("|")]
        if values and all(value and set(value) <= {"-", ":"} for value in values):
            continue
        rows.append([(value, len(rows) == 0, 1, 1) for value in values])
    return rows


def _table_rows(body: str) -> list[list[tuple[str, bool, int, int]]]:
    if "<table" in body.lower():
        parser = _TableHTMLParser()
        parser.feed(body)
        return parser.rows
    return _parse_markdown_table(body)


def _bbox(raw: object, *, width: float, height: float) -> BoundingBox | None:
    if not isinstance(raw, list | tuple) or len(raw) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(value) for value in raw)
    except (TypeError, ValueError):
        return None
    # MinerU content-list coordinates can use a 0..1000 normalized canvas.
    if max(x0, x1) > width + 0.5 or max(y0, y1) > height + 0.5:
        x0, x1 = x0 * width / 1000.0, x1 * width / 1000.0
        y0, y1 = y0 * height / 1000.0, y1 * height / 1000.0
    x0, x1 = max(0.0, x0), min(width, x1)
    y0, y1 = max(0.0, y0), min(height, y1)
    if x0 > x1 or y0 > y1:
        return None
    return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _cell_bbox(
    table_bbox: BoundingBox | None,
    row: int,
    column: int,
    rows: int,
    cols: int,
) -> BoundingBox | None:
    if table_bbox is None:
        return None
    cell_width = (table_bbox.x1 - table_bbox.x0) / cols
    cell_height = (table_bbox.y1 - table_bbox.y0) / rows
    return BoundingBox(
        x0=table_bbox.x0 + column * cell_width,
        y0=table_bbox.y0 + row * cell_height,
        x1=table_bbox.x0 + (column + 1) * cell_width,
        y1=table_bbox.y0 + (row + 1) * cell_height,
    )


def _payload_parts(payload: object) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if isinstance(payload, list):
        elements = [item for item in payload if isinstance(item, dict)]
        return [], elements
    if not isinstance(payload, dict):
        raise ParseError("MinerU output is not a JSON object/list", code=PDF_PARSE_FAILED)
    pages_raw = payload.get("pages", [])
    elements_raw = payload.get("elements", payload.get("content_list", []))
    pages = (
        [item for item in pages_raw if isinstance(item, dict)]
        if isinstance(pages_raw, list)
        else []
    )
    elements = (
        [item for item in elements_raw if isinstance(item, dict)]
        if isinstance(elements_raw, list)
        else []
    )
    return pages, elements


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float | str):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return default


def _as_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float | str):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return default


def convert_mineru_payload(
    payload: object,
    *,
    document_id: UUID,
    parser_version: str,
    model_revisions: dict[str, str],
    title: str = "MinerU document",
) -> DocumentIR:
    """Convert deterministic MinerU content-list JSON to Canonical IR."""
    pages_raw, items = _payload_parts(payload)
    page_dimensions: dict[int, tuple[float, float]] = {}
    for page_data in pages_raw:
        index = _as_int(page_data.get("page_idx", page_data.get("page", 0)))
        page_dimensions[index] = (
            _as_float(page_data.get("width"), 612.0),
            _as_float(page_data.get("height"), 792.0),
        )
    for item in items:
        index = _as_int(item.get("page_idx", item.get("page", 0)))
        size = item.get("page_size")
        if index not in page_dimensions and isinstance(size, list | tuple) and len(size) == 2:
            page_dimensions[index] = (
                _as_float(size[0], 612.0),
                _as_float(size[1], 792.0),
            )
    if not page_dimensions:
        max_page = max((_as_int(item.get("page_idx")) for item in items), default=0)
        page_dimensions = {index: (612.0, 792.0) for index in range(max_page + 1)}

    manifest = ParserManifest(
        parser_id="mineru",
        parser_version=parser_version,
        model_ids={"backend": "mineru-pipeline"},
        model_revisions=model_revisions,
        options={"isolated": True},
        signature=compute_parser_signature(
            parser_id="mineru",
            parser_version=parser_version,
            model_ids={"backend": "mineru-pipeline"},
            model_revisions=model_revisions,
            options={"isolated": True},
        ),
    )
    pages = [
        PageIR(physical_page=index + 1, width=size[0], height=size[1])
        for index, size in sorted(page_dimensions.items())
    ]
    page_map = {page.physical_page: page for page in pages}
    elements: list[DocumentElement] = []
    section_path: list[str] = []
    warnings: list[str] = []
    malformed = 0
    replacement_count = 0

    for item in items:
        page_index = _as_int(item.get("page_idx", item.get("page", 0)))
        physical_page = page_index + 1
        page_ir = page_map.get(physical_page)
        if page_ir is None:
            continue
        width, height = page_ir.width, page_ir.height
        source = SourceSpan(
            physical_page=physical_page,
            bbox=_bbox(item.get("bbox"), width=width, height=height),
            parser_element_id=str(item.get("id", "")) or None,
        )
        item_type = str(item.get("type", item.get("category_type", "text"))).lower()
        raw_text = str(item.get("text", item.get("content", "")))
        level = item.get("text_level")
        is_heading = isinstance(level, int) and level >= 1
        if item_type in {"title", "heading"} or is_heading:
            normalized = normalize_for_retrieval(raw_text)
            replacement_count += normalized.replacement_char_count
            resolved_level = _as_int(level, 1) if is_heading else 1
            section_path = section_path[: max(0, resolved_level - 1)]
            element = DocumentElement(
                kind="title" if item_type == "title" else "heading",
                reading_order=len(elements),
                raw_text=raw_text,
                normalized_text=normalized.text,
                section_path=list(section_path),
                provenance=[source],
                metadata={"mineru_type": item_type, "heading_level": resolved_level},
            )
            section_path.append(normalized.text)
        elif item_type in {"table", "table_body"}:
            body = str(item.get("table_body", item.get("html", raw_text)))
            rows = _table_rows(body)
            if not rows:
                malformed += 1
                warnings.append(PDF_TABLE_INVALID)
                continue
            occupied: set[tuple[int, int]] = set()
            cells: list[TableCell] = []
            max_column = 0
            for row_index, row in enumerate(rows):
                column = 0
                for value, header, row_span, column_span in row:
                    while (row_index, column) in occupied:
                        column += 1
                    normalized = normalize_for_retrieval(value, allow_empty=True)
                    replacement_count += normalized.replacement_char_count
                    table_box = source.bbox
                    cells.append(
                        TableCell(
                            row=row_index,
                            column=column,
                            row_span=row_span,
                            column_span=column_span,
                            raw_text=value,
                            normalized_text=normalized.text,
                            is_column_header=header or row_index == 0,
                            is_row_header=column == 0 and row_index > 0,
                            provenance=[
                                SourceSpan(
                                    physical_page=physical_page,
                                    bbox=_cell_bbox(
                                        table_box, row_index, column, len(rows), max(1, len(row))
                                    ),
                                    parser_element_id=source.parser_element_id,
                                )
                            ],
                        )
                    )
                    for covered_row in range(row_index, row_index + row_span):
                        for covered_column in range(column, column + column_span):
                            occupied.add((covered_row, covered_column))
                    column += column_span
                    max_column = max(max_column, column)
            header_rows = sorted({cell.row for cell in cells if cell.is_column_header})
            caption_value = item.get("table_caption", item.get("caption"))
            caption: str | None
            if isinstance(caption_value, list):
                caption = " ".join(str(value) for value in caption_value if value)
            else:
                caption = str(caption_value) if caption_value else None
            table = make_table_data(
                cells,
                row_count=max(
                    len(rows), max((cell.row + cell.row_span for cell in cells), default=1)
                ),
                column_count=max_column,
                header_rows=header_rows,
                caption=caption,
                html=body if "<table" in body.lower() else None,
            )
            element = DocumentElement(
                kind="table",
                reading_order=len(elements),
                raw_text=table.markdown,
                normalized_text=normalize_for_retrieval(table.markdown).text,
                section_path=list(section_path),
                provenance=[source],
                table=table,
                metadata={"mineru_type": item_type},
            )
        else:
            if not raw_text.strip():
                continue
            normalized = normalize_for_retrieval(raw_text)
            replacement_count += normalized.replacement_char_count
            kind: ElementKind = (
                "formula"
                if item_type in {"equation", "formula", "interline_equation"}
                else "paragraph"
            )
            element = DocumentElement(
                kind=kind,
                reading_order=len(elements),
                raw_text=raw_text,
                normalized_text=normalized.text,
                section_path=list(section_path),
                provenance=[source],
                metadata={"mineru_type": item_type},
            )
        elements.append(element)
        page_ir.element_ids.append(element.id)

    hard_failures = [] if elements else ["no readable content extracted"]
    tables = [element.table for element in elements if element.table is not None]
    orphan_count, numeric_total = count_orphan_numeric_cells(tables)
    orphan_ratio = orphan_count / numeric_total if numeric_total else 0.0
    if orphan_ratio > 0.05:
        hard_failures.append(PDF_TABLE_INVALID)
    quality = LayoutQualityReport(
        replacement_character_count=replacement_count,
        broken_unicode_count=replacement_count,
        table_count=sum(element.table is not None for element in elements),
        malformed_table_count=malformed,
        orphan_numeric_ratio=orphan_ratio,
        repeated_header_footer_ratio=0.0,
        reading_order_confidence=1.0,
        warnings=warnings,
        hard_failures=hard_failures,
    )
    return DocumentIR(
        document_id=document_id,
        title=title,
        parser=manifest,
        pages=pages,
        elements=elements,
        quality=quality,
        metadata={"layout_parser": "mineru"},
    )


class MinerUParser:
    """DocumentParser backed by an operator-managed isolated MinerU CLI."""

    def __init__(
        self,
        *,
        storage_dir: Path,
        command: str,
        backend: str,
        timeout_seconds: int,
        parser_version: str,
        model_revision: str,
    ) -> None:
        self._storage_dir = Path(storage_dir)
        self._command = command
        self._backend = backend
        self._timeout_seconds = timeout_seconds
        self._parser_version = parser_version
        self._model_revision = model_revision

    @classmethod
    def from_settings(cls, settings: Any) -> MinerUParser:
        return cls(
            storage_dir=Path(settings.storage_dir),
            command=str(getattr(settings, "mineru_command", "mineru")),
            backend=str(getattr(settings, "mineru_backend", "pipeline")),
            timeout_seconds=int(getattr(settings, "mineru_timeout_seconds", 900)),
            parser_version=str(getattr(settings, "mineru_parser_version", "")),
            model_revision=str(getattr(settings, "mineru_model_revision", "")),
        )

    @property
    def manifest(self) -> ParserManifest:
        model_revisions = {"pipeline": self._model_revision}
        return ParserManifest(
            parser_id="mineru",
            parser_version=self._parser_version,
            model_ids={"backend": self._backend},
            model_revisions=model_revisions,
            options={"backend": self._backend, "isolated": True},
            signature=compute_parser_signature(
                parser_id="mineru",
                parser_version=self._parser_version,
                model_ids={"backend": self._backend},
                model_revisions=model_revisions,
                options={"backend": self._backend, "isolated": True},
            ),
        )

    def parse(self, path: Path, *, document_id: UUID) -> DocumentIR:
        if not self._parser_version or not self._model_revision:
            raise ParseError(
                "MinerU parser version and model revision must be operator-pinned",
                code=PDF_PARSER_UNAVAILABLE,
            )
        uploads = (self._storage_dir / "uploads").resolve()
        resolved_input = path.resolve()
        if not resolved_input.is_relative_to(uploads) or not resolved_input.is_file():
            raise ParseError(
                "MinerU input must be a regular file below storage/uploads",
                code=PDF_PARSER_UNAVAILABLE,
            )
        output_dir = (self._storage_dir / "tmp" / "mineru" / str(document_id)).resolve()
        allowed_tmp = (self._storage_dir / "tmp").resolve()
        if not output_dir.is_relative_to(allowed_tmp) or output_dir.exists():
            raise ParseError(
                "MinerU output directory is unsafe or already exists", code=PDF_PARSE_FAILED
            )
        # MinerU owns creation of its -o directory. Create only the trusted
        # parent so a CLI cannot accidentally merge into a stale job result.
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        argv = build_mineru_argv(
            command=self._command,
            input_path=resolved_input,
            output_dir=output_dir,
            backend=self._backend,
        )
        try:
            result = subprocess.run(
                argv,
                check=False,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ParseError("MinerU parser timed out", code=PDF_PARSE_FAILED) from exc
        except OSError as exc:
            raise ParseError(
                "MinerU executable is unavailable", code=PDF_PARSER_UNAVAILABLE
            ) from exc
        if result.returncode != 0:
            raise ParseError(
                f"MinerU parser exited with status {result.returncode}", code=PDF_PARSE_FAILED
            )

        candidates = sorted(output_dir.rglob("*_content_list.json"))
        if not candidates:
            direct = output_dir / "content_list.json"
            candidates = [direct] if direct.is_file() else []
        if len(candidates) != 1 or not candidates[0].resolve().is_relative_to(output_dir):
            raise ParseError("MinerU produced no unique content-list JSON", code=PDF_PARSE_FAILED)
        try:
            payload = json.loads(candidates[0].read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ParseError("MinerU output JSON is invalid", code=PDF_PARSE_FAILED) from exc
        ir = convert_mineru_payload(
            payload,
            document_id=document_id,
            parser_version=self._parser_version,
            model_revisions={"pipeline": self._model_revision},
            title=path.stem,
        )
        validation = validate_document_ir(ir)
        if validation.issues:
            raise ParseError(
                "MinerU output failed Canonical IR validation", code=PDF_LAYOUT_INVALID
            )
        return ir


__all__ = ["MinerUParser", "build_mineru_argv", "convert_mineru_payload"]

"""PyMuPDF V2 adapter producing Canonical Document IR (spec §7.1).

Extraction (``_extract_pages_payload``) touches pymupdf and must run inside
the ARQ worker process or a subprocess — never the API event loop. Assembly
(``build_document_ir``) is a pure function over JSON-serializable primitives
so it is unit-testable without pymupdf and without real PDFs.

The adapter detects columns, removes repeated headers/footers into typed
elements, reconstructs paragraphs from line spans using bbox/font rules
(§8.3), builds tables from ``page.find_tables()`` with verified cell
coordinates, computes the §5.2 quality report, and never performs OCR.
"""

from __future__ import annotations

import re
import statistics
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID

from app.document_ir.errors import OCR_REQUIRED, ParseError
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
    TableData,
)
from app.document_ir.normalize import (
    NormalizeResult,
    normalize_for_retrieval,
)
from app.document_ir.serialize import compute_parser_signature

# --- Tuning constants (deterministic, documented) -------------------------

_HEADER_FOOTER_BAND = 72.0  # points from page edge considered top/bottom band
_REPEAT_MIN_PAGES = 2
_REPEAT_MIN_PAGE_RATIO = 0.6
_COLUMN_CROSSING_RATIO_MAX = 0.15
_COLUMN_MIN_BLOCKS_PER_SIDE = 2
_HEADING_SIZE_FACTOR = 1.15
_HEADING_MAX_CHARS = 200
_LINE_GAP_FACTOR = 1.8  # max baseline gap as multiple of median line height
_FONT_SIZE_DIFF = 0.20
_BODY_MIN_CHARS_FOR_OCR_CHECK = 200
_OCR_EMPTY_PAGE_RATIO = 0.80

_BULLET_RE = re.compile(r"^(?:[-*•]|\d+[.)])\s+")
_CAPTION_RE = re.compile(r"^(?:Table|Figure|Fig\.|表|图)\s*\S{0,12}")
_NUMERIC_RE = re.compile(r"^[±+]?\d")
_FORMULA_CHARS = set("εθΣμΠπΦφ±≤≥≠∑∫∂∇−×÷≈√")


def _looks_like_formula(text: str) -> bool:
    """Conservative formula heuristic: multiple math symbols, or an equals
    sign accompanied by at least one symbol (§8.2 formula elements)."""
    stripped = text.strip()
    if not stripped or len(stripped) > 300:
        return False
    symbol_count = sum(ch in _FORMULA_CHARS for ch in stripped)
    return symbol_count >= 2 or ("=" in stripped and symbol_count >= 1)


FAST_PATH_MIN_READING_ORDER_CONFIDENCE = 0.95
FAST_PATH_MAX_ORPHAN_NUMERIC_RATIO = 0.05
FAST_PATH_MAX_REPLACEMENT_CHARACTERS = 0


class _HasParse(Protocol):
    def parse(self, path: Path, *, document_id: UUID) -> DocumentIR: ...


def _bbox(values: list[float]) -> BoundingBox:
    return BoundingBox(x0=values[0], y0=values[1], x1=values[2], y1=values[3])


# --- Extraction primitives (pymupdf side) --------------------------------


def _extract_pages_payload(path: Path) -> dict[str, Any]:
    """Extract JSON-serializable page primitives via pymupdf.

    Runs pymupdf in whatever process calls this; unit tests exercise the pure
    builder instead, and subprocess tests cover this function end to end.
    """
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - dependency guarded by uv sync
        raise ParseError("PyMuPDF is not installed", code="PDF_PARSER_UNAVAILABLE") from exc

    if not path.exists():
        raise ParseError(f"file not found: {path.name}", code="PDF_PARSE_FAILED")

    doc = pymupdf.open(str(path))  # type: ignore[no-untyped-call]
    try:
        pages: list[dict[str, Any]] = []
        for number in range(doc.page_count):
            page = doc[number]
            width, height = float(page.rect.width), float(page.rect.height)

            blocks: list[dict[str, Any]] = []
            raw = page.get_text("dict")  # type: ignore[no-untyped-call]
            for block in raw.get("blocks", []):
                if block.get("type") != 0:
                    continue
                lines: list[dict[str, Any]] = []
                for line in block.get("lines", []):
                    spans = [
                        {
                            "text": span.get("text", ""),
                            "size": float(span.get("size", 10.0)),
                            "font": span.get("font", ""),
                            "bbox": [float(v) for v in span["bbox"]],
                        }
                        for span in line.get("spans", [])
                        if span.get("text", "").strip()
                    ]
                    if not spans:
                        continue
                    lines.append(
                        {
                            "bbox": [float(v) for v in line["bbox"]],
                            "spans": spans,
                            "text": "".join(s["text"] for s in spans),
                        }
                    )
                if lines:
                    blocks.append({"bbox": [float(v) for v in block["bbox"]], "lines": lines})

            tables: list[dict[str, Any]] = []
            found = page.find_tables()  # type: ignore[no-untyped-call]
            for tab in found.tables:
                rows_text = tab.extract()
                row_boxes: list[list[list[float] | None]] = []
                geometry_verified = True
                pymupdf_rows = getattr(tab, "rows", None) or []
                if len(pymupdf_rows) != len(rows_text):
                    geometry_verified = False
                    row_boxes = [[None] * int(tab.col_count) for _ in rows_text]
                else:
                    for trow in pymupdf_rows:
                        cells_raw = getattr(trow, "cells", None) or []
                        if len(cells_raw) != int(tab.col_count):
                            geometry_verified = False
                            row_boxes.append([None] * int(tab.col_count))
                            continue
                        row_boxes.append(
                            [
                                [float(v) for v in cell[:4]] if cell is not None else None
                                for cell in cells_raw
                            ]
                        )
                table_bbox_verified = (
                    all(v >= -1.0 for v in map(float, tab.bbox))
                    and float(tab.bbox[2]) <= width + 1.0
                    and float(tab.bbox[3]) <= height + 1.0
                )
                tables.append(
                    {
                        "bbox": [float(v) for v in tab.bbox],
                        "row_count": int(tab.row_count),
                        "col_count": int(tab.col_count),
                        "rows_text": [[str(c) for c in row] for row in rows_text],
                        "cell_bboxes": row_boxes,
                        "geometry_verified": geometry_verified and bool(table_bbox_verified),
                    }
                )

            pages.append(
                {
                    "physical_page": number + 1,
                    "width": width,
                    "height": height,
                    "blocks": blocks,
                    "tables": tables,
                }
            )
        return {"page_count": doc.page_count, "pages": pages}
    finally:
        doc.close()  # type: ignore[no-untyped-call]


# --- Pure assembly (no pymupdf) -------------------------------------------


def _line_size(line: dict[str, Any]) -> float:
    sizes = [span["size"] for span in line["spans"] if span["text"].strip()]
    return statistics.median(sizes) if sizes else 0.0


def detect_columns(blocks: list[dict[str, Any]], page_width: float) -> tuple[int, float]:
    """Return ``(column_count, crossing_ratio)`` for one page (§7.1)."""
    if len(blocks) < 4:
        return 1, 0.0
    mid = page_width / 2.0
    eps = 2.0
    crossing = 0
    left = right = 0
    for block in blocks:
        x0, _, x1, _ = block["bbox"]
        crosses = x0 < mid - eps and x1 > mid + eps
        if crosses:
            crossing += 1
        elif x1 <= mid:
            left += 1
        else:
            right += 1
    ratio = crossing / len(blocks)
    if (
        ratio <= _COLUMN_CROSSING_RATIO_MAX
        and left >= _COLUMN_MIN_BLOCKS_PER_SIDE
        and right >= _COLUMN_MIN_BLOCKS_PER_SIDE
    ):
        return 2, ratio
    return 1, ratio


def assign_column(block_bbox: list[float], page_width: float, column_count: int) -> int:
    if column_count <= 1:
        return 0
    center = (block_bbox[0] + block_bbox[2]) / 2.0
    return 0 if center < page_width / 2.0 else 1


def find_repeated_band_texts(
    pages: list[dict[str, Any]], normalizer: Any
) -> tuple[dict[str, str], float]:
    """Detect whole-block texts repeated in the top/bottom bands across pages.

    Returns ``(kind_by_key, ratio)`` where ``kind_by_key`` maps
    ``"<normalized>@<page>"`` → "header"/"footer".
    """
    total_pages = len(pages)
    min_repeats = max(_REPEAT_MIN_PAGES, int(total_pages * _REPEAT_MIN_PAGE_RATIO))
    if total_pages < 2:
        return {}, 0.0

    band_hits: dict[str, dict[int, str]] = {}
    for page in pages:
        number = page["physical_page"]
        height = page["height"]
        for block in page["blocks"]:
            y0, y1 = block["bbox"][1], block["bbox"][3]
            if y1 > _HEADER_FOOTER_BAND and y0 < height - _HEADER_FOOTER_BAND:
                continue
            position = "header" if y1 <= _HEADER_FOOTER_BAND else "footer"
            text = "\n".join(line["text"] for line in block["lines"])
            key = normalizer(text, allow_empty=True).text
            if not key:
                continue
            existing = band_hits.get(key, {}).get(number)
            if existing is None:
                band_hits.setdefault(key, {})[number] = position

    kind_by_key: dict[str, str] = {}
    occurrences = 0
    for key, by_page in band_hits.items():
        if len(by_page) >= min_repeats:
            for number, position in by_page.items():
                kind_by_key[f"{key}@{number}"] = position
                occurrences += 1
    ratio = occurrences / (total_pages * 2) if total_pages else 0.0
    return kind_by_key, min(ratio, 1.0)


def merge_lines_into_paragraphs(
    lines: list[dict[str, Any]],
    *,
    body_size: float,
    column_ids: list[int],
) -> list[list[dict[str, Any]]]:
    """Group consecutive lines into paragraph groups per §8.3 conditions."""
    if not lines:
        return []
    heights = [max(line["bbox"][3] - line["bbox"][1], 1.0) for line in lines]
    median_height = statistics.median(heights) or 1.0
    max_gap = median_height * _LINE_GAP_FACTOR

    groups: list[list[dict[str, Any]]] = [[lines[0]]]
    for index in range(1, len(lines)):
        previous, current = lines[index - 1], lines[index]
        same_column = column_ids[index] == column_ids[index - 1]
        gap = current["bbox"][1] - previous["bbox"][3]
        prev_size, curr_size = _line_size(previous), _line_size(current)
        size_close = abs(curr_size - prev_size) <= max(prev_size, curr_size) * _FONT_SIZE_DIFF
        prev_is_heading = (
            prev_size >= body_size * _HEADING_SIZE_FACTOR
            and len(previous["text"]) <= _HEADING_MAX_CHARS
        )
        prev_is_caption = bool(_CAPTION_RE.match(previous["text"].strip()))
        curr_is_list = bool(_BULLET_RE.match(current["text"].lstrip()))

        can_merge = (
            same_column
            and -1.0 <= gap <= max_gap
            and size_close
            and not prev_is_heading
            and not prev_is_caption
            and not curr_is_list
        )
        if can_merge:
            groups[-1].append(current)
        else:
            groups.append([current])
    return groups


def classify_line_kind(text: str, size: float, body_size: float) -> ElementKind:
    stripped = text.strip()
    if _looks_like_formula(stripped):
        return "formula"
    if _BULLET_RE.match(stripped):
        return "list"
    if size >= body_size * _HEADING_SIZE_FACTOR and len(stripped) <= _HEADING_MAX_CHARS:
        return "heading"
    if _CAPTION_RE.match(stripped) and len(stripped) <= 300:
        return "caption"
    return "paragraph"


def compute_body_size(lines: list[dict[str, Any]]) -> float:
    """Body font size ≈ 25th percentile of line sizes.

    The dominant body text is the most frequent *smaller* size; using the
    median lets headings on short pages inflate the baseline and suppress
    heading detection.
    """
    sizes = [_line_size(line) for line in lines if _line_size(line) > 0]
    if not sizes:
        return 10.0
    if len(sizes) == 1:
        return sizes[0]
    quartiles = statistics.quantiles(sizes, n=4)
    return quartiles[0]


def header_row_count(rows_text: list[list[str]]) -> int:
    """Rows before the first numeric-looking cell form the header block.

    Tables with no numeric cells treat their first row as the header.
    """
    for index, row in enumerate(rows_text):
        if any(_NUMERIC_RE.match(cell.strip()) for cell in row if cell.strip()):
            return index
    return 1 if rows_text else 0


def build_table_data_from_primitive(
    table: dict[str, Any], page: int
) -> tuple[TableData | None, list[str]]:
    """Build TableData from an extraction primitive; never fabricate cells."""
    warnings: list[str] = []
    rows_text = table["rows_text"]
    row_count, col_count = table["row_count"], table["col_count"]
    if row_count < 1 or col_count < 1 or not rows_text:
        warnings.append("PDF_TABLE_INVALID: empty extraction")
        return None, warnings
    if any(len(row) != col_count for row in rows_text):
        warnings.append("PDF_TABLE_INVALID: ragged extraction")
        return None, warnings

    header_rows = list(range(header_row_count(rows_text)))
    cells: list[TableCell] = []
    cell_bboxes = table.get("cell_bboxes") or []

    for r, row in enumerate(rows_text):
        boxes_for_row = cell_bboxes[r] if r < len(cell_bboxes) else []
        for c, text in enumerate(row):
            box_list = boxes_for_row[c] if c < len(boxes_for_row) else None
            provenance: list[SourceSpan] = []
            if box_list is not None:
                provenance.append(SourceSpan(physical_page=page, bbox=_bbox(box_list)))
            normalized: NormalizeResult = normalize_for_retrieval(text, allow_empty=True)
            cells.append(
                TableCell(
                    row=r,
                    column=c,
                    raw_text=text,
                    normalized_text=normalized.text,
                    is_column_header=r in header_rows,
                    provenance=provenance,
                )
            )

    if not table.get("geometry_verified"):
        warnings.append("PDF_TABLE_INVALID: cell coordinates could not be verified")
    try:
        built = make_table_data(
            cells,
            row_count=row_count,
            column_count=col_count,
            header_rows=header_rows,
        )
    except Exception as exc:
        warnings.append(f"PDF_TABLE_INVALID: {exc}")
        return None, warnings
    return built, warnings


def count_orphan_numeric_cells(tables: list[TableData]) -> tuple[int, int]:
    """Numeric cells without resolvable header/row paths (spec §9.1)."""
    orphans = 0
    total = 0
    for table in tables:
        grid: dict[tuple[int, int], TableCell] = {
            (cell.row, cell.column): cell for cell in table.cells
        }
        header_columns: set[int] = set()
        last_header = max(table.header_rows) if table.header_rows else -1
        for cell in table.cells:
            if cell.is_column_header and cell.normalized_text.strip():
                header_columns.add(cell.column)
        for cell in table.cells:
            if cell.is_header or not _NUMERIC_RE.match(cell.normalized_text.strip()):
                continue
            total += 1
            has_column_header = cell.column in header_columns and last_header >= 0
            row_label = grid.get((cell.row, 0))
            has_row_label = (
                row_label is not None
                and not row_label.is_column_header
                and bool(row_label.normalized_text.strip())
                and not _NUMERIC_RE.match(row_label.normalized_text.strip())
            )
            if not (has_column_header and has_row_label):
                orphans += 1
    return orphans, total


def build_document_ir(
    payload: dict[str, Any],
    *,
    document_id: UUID,
    title: str,
    parser_version: str,
    max_orphan_ratio: float = FAST_PATH_MAX_ORPHAN_NUMERIC_RATIO,
    max_replacement_characters: int = FAST_PATH_MAX_REPLACEMENT_CHARACTERS,
) -> DocumentIR:
    """Assemble the canonical IR from extraction primitives (pure)."""
    manifest = ParserManifest(
        parser_id="pymupdf",
        parser_version=parser_version,
        model_ids={},
        model_revisions={},
        options={"ocr": False},
        signature=compute_parser_signature(
            parser_id="pymupdf", parser_version=parser_version, options={"ocr": False}
        ),
    )

    elements: list[DocumentElement] = []
    pages_ir: list[PageIR] = []
    warnings: list[str] = []
    hard_failures: list[str] = []
    reading_order = 0
    replacement_total = 0
    broken_unicode_total = 0
    tables_ir: list[TableData] = []
    malformed_tables = 0

    # Pass 1: flatten lines, detect columns, compute body size.
    column_counts: dict[int, tuple[int, float]] = {}
    all_lines: list[dict[str, Any]] = []
    for page in payload["pages"]:
        number = page["physical_page"]
        flat: list[dict[str, Any]] = [line for block in page["blocks"] for line in block["lines"]]
        column_counts[number] = detect_columns(page["blocks"], page["width"])
        all_lines.extend(flat)

    body_size = compute_body_size(all_lines)

    # Pass 2: repeated header/footer detection (block level, §7.1).
    header_footer_map, repeat_ratio = find_repeated_band_texts(
        payload["pages"], normalize_for_retrieval
    )

    # Pass 3: per-page elements.
    # Heading hierarchy by font size: each distinct size is a level; equal
    # sizes are siblings (replace), smaller sizes nest under the nearest
    # larger heading still on record (§7.1 deterministic heuristic).
    heading_levels: dict[float, list[str]] = {}
    current_section_path: list[str] = []
    for page in payload["pages"]:
        page_number: int = page["physical_page"]
        column_count = column_counts[page_number][0]

        filtered: list[tuple[dict[str, Any], int]] = []
        for block in page["blocks"]:
            block_raw = "\n".join(line["text"] for line in block["lines"])
            normalized_block_key = normalize_for_retrieval(block_raw, allow_empty=True).text
            if f"{normalized_block_key}@{page_number}" in header_footer_map:
                kind = header_footer_map[f"{normalized_block_key}@{page_number}"]
                replacement_total += normalize_for_retrieval(
                    block_raw, allow_empty=True
                ).replacement_char_count
                elements.append(
                    DocumentElement(
                        kind=cast(ElementKind, kind),
                        reading_order=reading_order,
                        raw_text=block_raw,
                        normalized_text=normalized_block_key,
                        section_path=[],
                        provenance=[
                            SourceSpan(physical_page=page_number, bbox=_bbox(block["bbox"]))
                        ],
                    )
                )
                reading_order += 1
                continue
            for line in block["lines"]:
                filtered.append((line, assign_column(line["bbox"], page["width"], column_count)))

        filtered.sort(
            key=lambda pair: (
                pair[1],
                round(pair[0]["bbox"][1], 1),
                round(pair[0]["bbox"][0], 1),
            )
        )
        grouped = merge_lines_into_paragraphs(
            [ln for ln, _ in filtered],
            body_size=body_size,
            column_ids=[c for _, c in filtered],
        )

        for group in grouped:
            raw_text = "\n".join(line["text"] for line in group)
            normalized_group = normalize_for_retrieval(raw_text, allow_empty=True)
            replacement_total += normalized_group.replacement_char_count
            first = group[0]
            size = _line_size(first)
            kind = classify_line_kind(first["text"], size, body_size)
            if kind == "heading":
                larger = [s for s in heading_levels if s > size + 1e-6]
                prefix = heading_levels[min(larger)] if larger else []
                heading_levels[size] = [*prefix, normalized_group.text]
                current_section_path = heading_levels[size]
            section_path = current_section_path
            element = DocumentElement(
                kind=kind,
                reading_order=reading_order,
                raw_text=raw_text,
                normalized_text=normalized_group.text,
                section_path=section_path,
                provenance=[
                    SourceSpan(physical_page=page_number, bbox=_bbox(line["bbox"]))
                    for line in group
                ],
            )
            elements.append(element)
            reading_order += 1

        # Tables on this page.
        for table in page["tables"]:
            built, table_warnings = build_table_data_from_primitive(table, page_number)
            warnings.extend(table_warnings)
            if built is None:
                malformed_tables += 1
                continue
            tables_ir.append(built)
            table_normalized = normalize_for_retrieval(
                built.markdown.replace("|", " ").replace("\n", " "), allow_empty=True
            )
            replacement_total += table_normalized.replacement_char_count
            caption = built.caption or (built.cells[0].normalized_text if built.cells else "")
            elements.append(
                DocumentElement(
                    kind="table",
                    reading_order=reading_order,
                    raw_text=built.markdown,
                    normalized_text=caption or table_normalized.text,
                    section_path=current_section_path,
                    provenance=[SourceSpan(physical_page=page_number, bbox=_bbox(table["bbox"]))],
                    table=built,
                )
            )
            reading_order += 1

        pages_ir.append(
            PageIR(
                physical_page=page_number,
                width=page["width"],
                height=page["height"],
                element_ids=[
                    element.id
                    for element in elements
                    if any(span.physical_page == page_number for span in element.provenance)
                ],
            )
        )

    # Quality gates.
    orphan_count, numeric_total = count_orphan_numeric_cells(tables_ir)
    orphan_ratio = orphan_count / numeric_total if numeric_total else 0.0
    broken_unicode_total = sum(
        element.normalized_text.count("\ufffd") + element.raw_text.count("\ufffd")
        for element in elements
    )
    # Two-column layouts carry a small fixed uncertainty from the gutter
    # heuristic; single-column pages are fully trusted (§7.1 confidence).
    confidence_values = [
        max(0.0, 0.98 - crossing) if count > 1 else 1.0
        for _, (count, crossing) in column_counts.items()
    ]
    reading_confidence = min(confidence_values) if confidence_values else 0.0

    if not elements:
        hard_failures.append("no readable content extracted")
    if replacement_total > max_replacement_characters:
        hard_failures.append(
            f"replacement characters {replacement_total} exceed limit {max_replacement_characters}"
        )
    if orphan_ratio > max_orphan_ratio:
        hard_failures.append(
            f"orphan numeric ratio {orphan_ratio:.3f} exceeds limit {max_orphan_ratio}"
        )

    multi_col_pages = sum(1 for cc in column_counts.values() if cc[0] > 1)

    quality = LayoutQualityReport(
        replacement_character_count=replacement_total,
        broken_unicode_count=broken_unicode_total,
        table_count=len(tables_ir),
        malformed_table_count=malformed_tables,
        orphan_numeric_ratio=orphan_ratio,
        repeated_header_footer_ratio=min(repeat_ratio, 1.0),
        reading_order_confidence=max(0.0, min(1.0, reading_confidence)),
        warnings=[*warnings, f"multicolumn_pages:{multi_col_pages}"],
        hard_failures=hard_failures,
    )

    return DocumentIR(
        document_id=document_id,
        title=title,
        parser=manifest,
        pages=pages_ir,
        elements=elements,
        quality=quality,
        metadata={
            "column_counts": {str(k): v[0] for k, v in column_counts.items()},
            "body_font_size": body_size,
        },
    )


def check_ocr_required(payload: dict[str, Any]) -> None:
    """Raise OCR_REQUIRED when there is no usable text layer (spec §11.3)."""
    page_count = payload["page_count"]
    if page_count == 0:
        raise ParseError("PDF has no pages", code="OCR_REQUIRED")
    empty_pages = sum(1 for p in payload["pages"] if not p["blocks"] and not p["tables"])
    total_chars = sum(
        len(line["text"]) for p in payload["pages"] for b in p["blocks"] for line in b["lines"]
    )
    ratio = empty_pages / page_count
    if ratio > _OCR_EMPTY_PAGE_RATIO and total_chars < _BODY_MIN_CHARS_FOR_OCR_CHECK:
        raise ParseError(
            f"PDF has too little extractable text ({total_chars} chars, "
            f"{empty_pages}/{page_count} empty pages). OCR is not supported.",
            code=OCR_REQUIRED,
        )


def fast_path_acceptable(ir: DocumentIR, *, settings: Any = None) -> bool:
    """Fast-path activation gate (spec §7.1); all conditions must hold."""
    min_confidence = getattr(
        settings,
        "pdf_fast_path_min_reading_order_confidence",
        FAST_PATH_MIN_READING_ORDER_CONFIDENCE,
    )
    max_orphan = getattr(
        settings, "pdf_max_orphan_numeric_ratio", FAST_PATH_MAX_ORPHAN_NUMERIC_RATIO
    )
    max_replacements = getattr(
        settings, "pdf_max_replacement_characters", FAST_PATH_MAX_REPLACEMENT_CHARACTERS
    )
    quality = ir.quality
    return (
        quality.malformed_table_count == 0
        and quality.reading_order_confidence >= min_confidence
        and quality.replacement_character_count <= max_replacements
        and quality.orphan_numeric_ratio <= max_orphan
        and not quality.hard_failures
    )


class PyMuPDFParser:
    """V2 DocumentParser implementation over pymupdf (spec §7.1)."""

    def __init__(self, parser_version: str | None = None) -> None:
        self._override_version = parser_version
        self._manifest: ParserManifest | None = None

    @property
    def manifest(self) -> ParserManifest:
        if self._manifest is None:
            version = self._override_version or _installed_pymupdf_version()
            self._manifest = ParserManifest(
                parser_id="pymupdf",
                parser_version=version,
                model_ids={},
                model_revisions={},
                options={"ocr": False},
                signature=compute_parser_signature(
                    parser_id="pymupdf", parser_version=version, options={"ocr": False}
                ),
            )
        return self._manifest

    def parse(self, path: Path, *, document_id: UUID, title: str | None = None) -> DocumentIR:
        payload = _extract_pages_payload(path)
        check_ocr_required(payload)
        resolved_title = title or _derive_title(payload) or path.stem
        return build_document_ir(
            payload,
            document_id=document_id,
            title=resolved_title,
            parser_version=self.manifest.parser_version,
        )


def _installed_pymupdf_version() -> str:
    try:
        import pymupdf

        return str(pymupdf.__version__)
    except ImportError as exc:  # pragma: no cover
        raise ParseError("PyMuPDF is not installed", code="PDF_PARSER_UNAVAILABLE") from exc


def _derive_title(payload: dict[str, Any]) -> str:
    for page in payload["pages"]:
        for block in page["blocks"]:
            for line in block["lines"]:
                text: str = line["text"].strip()
                if text:
                    return text[:300]
    return ""


# --- Legacy bridge (comparison only; production V2 must not use it) --------


def bridge_to_legacy_paragraphs(ir: DocumentIR) -> dict[str, Any]:
    """Convert IR to legacy ParsedDocument-shaped dicts for A/B comparison.

    Deliberately returns plain dicts so importing the legacy dataclasses is
    unnecessary here; tests compare against ``app.loaders.base`` models.
    """
    paragraphs: list[dict[str, Any]] = []
    line_start = 1
    for element in ir.elements:
        content = element.raw_text
        line_count = content.count("\n") + 1
        page = element.provenance[0].physical_page if element.provenance else None
        paragraphs.append(
            {
                "type": "table" if element.kind == "table" else "text",
                "content": content,
                "page": page,
                "line_start": line_start,
                "line_end": line_start + line_count - 1,
                "metadata": {"ir_kind": element.kind},
            }
        )
        line_start += line_count + 1
    return {
        "title": ir.title,
        "paragraphs": paragraphs,
        "metadata": {
            "page_count": len(ir.pages),
            "character_count": sum(len(e.normalized_text) for e in ir.elements),
            "loader": "pymupdf-v2-bridge",
            "parser_signature": ir.parser.signature,
        },
    }


_: _HasParse = PyMuPDFParser(parser_version="0")

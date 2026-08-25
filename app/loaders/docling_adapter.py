"""Docling V2 adapter producing Canonical Document IR (spec §7.2).

Extraction (``load_docling_payload``) imports docling lazily and must run
inside the ARQ worker process or an explicit CLI — never the API event loop.
Assembly (``build_document_ir_from_docling``) is a pure function over the
DoclingDocument JSON export so it is unit-testable without docling and
without real models (CI uses deterministic fake payloads).

The adapter maps Docling labels to ElementKind, converts BOTTOMLEFT bounding
boxes to the IR top-left origin, walks ``body.children`` depth-first for
reading order, emits furniture as header/footer elements first, rebuilds
tables from ``data.grid`` with cell provenance, and never fabricates cells:
grids that do not match their declared shape produce ``PDF_TABLE_INVALID``
warnings and are counted malformed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from app.document_ir.errors import (
    OCR_REQUIRED,
    PDF_PARSE_FAILED,
    PDF_PARSER_OOM,
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
    TableData,
)
from app.document_ir.normalize import NormalizeResult, normalize_for_retrieval
from app.document_ir.serialize import compute_parser_signature
from app.loaders.pymupdf_adapter import count_orphan_numeric_cells

# Reading-order confidence starts at full trust in Docling's own order; every
# dangling/unresolvable reference found during the body walk costs 0.02
# (deterministic, documented).
_CONFIDENCE_PER_DANGLING_REF = 0.02

_LABEL_TO_KIND: dict[str, ElementKind] = {
    "title": "title",
    "section_header": "heading",
    "subtitle": "heading",
    "text": "paragraph",
    "paragraph": "paragraph",
    "reference": "paragraph",
    "footnote": "paragraph",
    "list_item": "list",
    "caption": "caption",
    "code": "code",
    "formula": "formula",
    "page_header": "header",
    "page_footer": "footer",
}

_FALLBACK_KIND: ElementKind = "paragraph"


# --- Extraction primitives (docling side) ----------------------------------


def _accelerator_device(device: str) -> Any:
    from docling.datamodel.accelerator_options import AcceleratorDevice

    normalized = device.strip().lower()
    if normalized == "cuda:0":
        return AcceleratorDevice.CUDA
    if normalized == "auto":
        return AcceleratorDevice.AUTO
    return AcceleratorDevice.CPU


def load_docling_payload(
    path: Path,
    *,
    ocr: bool = False,
    table_structure: bool = True,
    formula_enrichment: bool = True,
    device: str = "cpu",
    artifacts_path: str = "",
) -> dict[str, Any]:
    """Convert *path* with Docling and return its JSON-export payload.

    Raises :class:`ParseError` with stable codes; never leaks absolute paths.
    """
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:
        raise ParseError(
            "docling is not installed; install the 'pdf-layout' extra",
            code=PDF_PARSER_UNAVAILABLE,
        ) from exc

    if not path.exists():
        raise ParseError(f"file not found: {path.name}", code=PDF_PARSE_FAILED)

    options = PdfPipelineOptions()
    options.do_ocr = ocr
    options.do_table_structure = table_structure
    options.do_formula_enrichment = formula_enrichment
    options.accelerator_options.num_threads = 4
    options.accelerator_options.device = _accelerator_device(device)
    if artifacts_path:
        options.artifacts_path = artifacts_path

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
        }
    )
    try:
        result = converter.convert(str(path))
    except MemoryError as exc:
        raise ParseError(f"docling ran out of memory: {exc}", code=PDF_PARSER_OOM) from exc
    except Exception as exc:
        message = str(exc)
        lowered = message.lower()
        if "out of memory" in lowered:
            raise ParseError(message[:300], code=PDF_PARSER_OOM) from exc
        raise ParseError(
            f"docling failed to parse {path.name}: {type(exc).__name__}",
            code=PDF_PARSE_FAILED,
        ) from exc
    payload = result.document.export_to_dict()
    if not isinstance(payload, dict):  # pragma: no cover - docling contract
        raise ParseError("docling returned an unexpected document type", code=PDF_PARSE_FAILED)
    return payload


def check_docling_content(payload: dict[str, Any]) -> None:
    """Raise OCR_REQUIRED when no text layer exists while OCR is disabled."""
    has_content = bool(payload.get("texts") or payload.get("tables") or payload.get("pictures"))
    if not has_content:
        raise ParseError(
            "Docling extracted no text layer and OCR is disabled",
            code=OCR_REQUIRED,
        )


# --- Pure assembly (no docling) ---------------------------------------------


def _build_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for collection in ("texts", "tables", "pictures", "key_value_items", "form_items", "groups"):
        for position, item in enumerate(payload.get(collection) or []):
            if isinstance(item, dict):
                index[f"#/{collection}/{position}"] = item
    for name in ("body", "furniture"):
        node = payload.get(name)
        if isinstance(node, dict):
            index[f"#/{name}"] = node
    return index


def _convert_bbox(bbox: Any, page_width: float, page_height: float) -> BoundingBox | None:
    """Convert a Docling bbox (BOTTOMLEFT origin) to the IR top-left origin."""
    if not isinstance(bbox, dict):
        return None
    try:
        left = float(bbox["l"])
        right = float(bbox["r"])
        top = float(bbox["t"])
        bottom = float(bbox["b"])
    except (KeyError, TypeError, ValueError):
        return None
    origin = str(bbox.get("coord_origin", "BOTTOMLEFT")).upper()
    if origin == "TOPLEFT":
        y0, y1 = top, bottom
    else:
        y0, y1 = page_height - bottom, page_height - top
    x0 = min(max(left, 0.0), page_width)
    x1 = min(max(right, 0.0), page_width)
    y0 = min(max(y0, 0.0), page_height)
    y1 = min(max(y1, 0.0), page_height)
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _provenance_spans(item: dict[str, Any], pages: dict[int, PageIR]) -> list[SourceSpan]:
    spans: list[SourceSpan] = []
    for prov in item.get("prov") or []:
        if not isinstance(prov, dict):
            continue
        page_no = prov.get("page_no")
        page = pages.get(page_no) if isinstance(page_no, int) else None
        if page is None:
            continue
        spans.append(
            SourceSpan(
                physical_page=page.physical_page,
                bbox=_convert_bbox(prov.get("bbox"), page.width, page.height),
            )
        )
    return spans


def _map_kind(label: str, content_layer: str, collection: str) -> ElementKind:
    if collection == "pictures":
        return "figure"
    kind = _LABEL_TO_KIND.get(label, _FALLBACK_KIND)
    if content_layer == "furniture" and kind not in {"header", "footer"}:
        return "header"
    return kind


def _table_header_rows(grid_rows: list[list[dict[str, Any]]]) -> list[int]:
    """Leading rows whose non-empty cells are all column headers."""
    header_rows: list[int] = []
    for row in grid_rows:
        meaningful = [cell for cell in row if str(cell.get("text", "")).strip()]
        if meaningful and all(cell.get("column_header") for cell in meaningful):
            header_rows.append(len(header_rows))
        else:
            break
    if not header_rows and grid_rows:
        header_rows = [0]
    return header_rows


def _resolve_caption_text(
    table_item: dict[str, Any], index: dict[str, dict[str, Any]]
) -> str | None:
    for ref in table_item.get("captions") or []:
        ref_str = ref.get("$ref") if isinstance(ref, dict) else None
        if not isinstance(ref_str, str):
            continue
        target = index.get(ref_str)
        if target is None:
            continue
        candidate = str(target.get("text", "")).strip()
        if candidate:
            return candidate
    return None


def build_table_from_docling(
    table_item: dict[str, Any],
    pages: dict[int, PageIR],
    index: dict[str, dict[str, Any]],
) -> tuple[TableData | None, list[str], list[SourceSpan]]:
    """Build TableData from a Docling table item; never fabricate cells."""
    data = table_item.get("data") or {}
    raw_rows = data.get("num_rows")
    raw_cols = data.get("num_cols")
    grid = data.get("grid") or []
    warnings: list[str] = []

    if not isinstance(raw_rows, int) or not isinstance(raw_cols, int):
        warnings.append(f"{PDF_TABLE_INVALID}: grid does not match declared shape")
        return None, warnings, []
    row_count, column_count = int(raw_rows), int(raw_cols)
    shape_invalid = (
        row_count < 1
        or column_count < 1
        or len(grid) != row_count
        or any(not isinstance(row, list) or len(row) != column_count for row in grid)
    )
    if shape_invalid:
        warnings.append(f"{PDF_TABLE_INVALID}: grid does not match declared shape")
        return None, warnings, []

    header_rows = _table_header_rows(grid)
    seen_objects: set[int] = set()
    cells: list[TableCell] = []
    table_spans = _provenance_spans(table_item, pages)
    page_for_cells = table_spans[0].physical_page if table_spans else 1
    page = pages.get(page_for_cells)

    for r, row in enumerate(grid):
        for c, raw_cell in enumerate(row):
            if not isinstance(raw_cell, dict):
                continue
            object_key = id(raw_cell)
            if object_key in seen_objects:
                continue
            seen_objects.add(object_key)
            start_row = int(raw_cell.get("start_row_offset_idx", r))
            start_col = int(raw_cell.get("start_col_offset_idx", c))
            row_span = int(
                raw_cell.get("row_span")
                or max(int(raw_cell.get("end_row_offset_idx", start_row)) - start_row, 1)
            )
            col_span = int(
                raw_cell.get("col_span")
                or max(int(raw_cell.get("end_col_offset_idx", start_col)) - start_col, 1)
            )
            text_value = str(raw_cell.get("text", ""))
            normalized_result: NormalizeResult = normalize_for_retrieval(
                text_value, allow_empty=True
            )
            cell_provenance: list[SourceSpan] = []
            if page is not None:
                converted = _convert_bbox(raw_cell.get("bbox"), page.width, page.height)
                if converted is not None:
                    cell_provenance.append(SourceSpan(physical_page=page_for_cells, bbox=converted))
            cells.append(
                TableCell(
                    row=start_row,
                    column=start_col,
                    row_span=max(row_span, 1),
                    column_span=max(col_span, 1),
                    raw_text=text_value,
                    normalized_text=normalized_result.text,
                    is_column_header=bool(raw_cell.get("column_header")),
                    is_row_header=bool(raw_cell.get("row_header")),
                    provenance=cell_provenance,
                )
            )

    try:
        built = make_table_data(
            cells,
            row_count=row_count,
            column_count=column_count,
            header_rows=header_rows,
            caption=_resolve_caption_text(table_item, index),
        )
    except Exception as exc:
        warnings.append(f"{PDF_TABLE_INVALID}: {exc}")
        return None, warnings, []
    return built, warnings, table_spans


def build_document_ir_from_docling(
    payload: dict[str, Any],
    *,
    document_id: UUID,
    title: str,
    manifest: ParserManifest,
) -> DocumentIR:
    """Assemble the canonical IR from a DoclingDocument export (pure)."""
    index = _build_index(payload)

    # Pages: export uses {"pages": {"<n>": {"size": {...}, "page_no": n}}}.
    pages_by_number: dict[int, PageIR] = {}
    raw_pages = payload.get("pages") or {}
    page_items = raw_pages.values() if isinstance(raw_pages, dict) else raw_pages
    for raw in page_items:
        if not isinstance(raw, dict):
            continue
        number = raw.get("page_no")
        size = raw.get("size") or {}
        width = float(size.get("width", 0.0))
        height = float(size.get("height", 0.0))
        if not isinstance(number, int) or width <= 0 or height <= 0:
            continue
        pages_by_number[number] = PageIR(physical_page=number, width=width, height=height)

    elements: list[DocumentElement] = []
    warnings: list[str] = []
    hard_failures: list[str] = []
    replacement_total = 0
    broken_unicode_total = 0
    reading_order = 0
    dangling_refs = 0
    tables_ir: list[TableData] = []
    malformed_tables = 0

    heading_stack: list[tuple[int, str]] = []

    def register_text_stats(raw_text: str, normalized_result: NormalizeResult) -> int:
        """Broken-unicode (U+FFFD) occurrences across raw and normalized text."""
        return raw_text.count("\ufffd") + normalized_result.text.count("\ufffd")

    def emit_table(table_item: dict[str, Any], collection_ref: str) -> None:
        nonlocal reading_order, replacement_total, broken_unicode_total, malformed_tables
        built, table_warnings, spans = build_table_from_docling(table_item, pages_by_number, index)
        warnings.extend(table_warnings)
        if built is None:
            malformed_tables += 1
            return
        tables_ir.append(built)
        markdown_normalized = normalize_for_retrieval(
            built.markdown.replace("|", " ").replace("\n", " "), allow_empty=True
        )
        broken_unicode_total += built.markdown.count("\ufffd") + markdown_normalized.text.count(
            "\ufffd"
        )
        replacement_total += markdown_normalized.replacement_char_count
        elements.append(
            DocumentElement(
                kind="table",
                reading_order=reading_order,
                raw_text=built.markdown,
                normalized_text=built.caption or markdown_normalized.text,
                section_path=[text for _, text in heading_stack],
                provenance=spans,
                table=built,
                metadata={"docling_ref": collection_ref},
            )
        )
        reading_order += 1

    def emit_item(item: dict[str, Any], collection: str, ref: str) -> None:
        nonlocal reading_order, replacement_total, broken_unicode_total
        if collection == "tables":
            emit_table(item, ref)
            return
        label = str(item.get("label", ""))
        layer = str(item.get("content_layer", "body"))
        kind = _map_kind(label, layer, collection)
        raw_text = str(item.get("orig", item.get("text", "")))
        normalized_result = normalize_for_retrieval(raw_text, allow_empty=True)
        broken_unicode_total += register_text_stats(raw_text, normalized_result)
        replacement_total += normalized_result.replacement_char_count

        level_raw = item.get("level")
        level = level_raw if isinstance(level_raw, int) and level_raw >= 1 else None

        if kind == "heading":
            resolved_level = level if level is not None else len(heading_stack) + 1
            while heading_stack and heading_stack[-1][0] >= resolved_level:
                heading_stack.pop()
            section_path = [text for _, text in heading_stack]
        elif kind in {"title", "header", "footer"}:
            section_path = []
        else:
            section_path = [text for _, text in heading_stack]

        metadata: dict[str, object] = {"docling_ref": ref}
        if kind == "heading" and level is not None:
            metadata["docling_level"] = level

        elements.append(
            DocumentElement(
                kind=kind,
                reading_order=reading_order,
                raw_text=raw_text,
                normalized_text=normalized_result.text,
                section_path=section_path,
                provenance=_provenance_spans(item, pages_by_number),
                metadata=metadata,
            )
        )
        reading_order += 1

        if kind == "heading":
            heading_stack.append((resolved_level, normalized_result.text))

    def visit(ref_node: Any, visiting: set[str]) -> None:
        nonlocal dangling_refs
        if isinstance(ref_node, dict):
            ref = ref_node.get("$ref")
        elif isinstance(ref_node, str):
            ref = ref_node
        else:
            ref = None
        if not isinstance(ref, str):
            dangling_refs += 1
            warnings.append("malformed child reference in document tree")
            return
        node = index.get(ref)
        if node is None:
            dangling_refs += 1
            warnings.append(f"dangling reference skipped: {ref}")
            return
        if ref in visiting:
            dangling_refs += 1
            warnings.append(f"cyclic reference skipped: {ref}")
            return
        visiting.add(ref)
        try:
            children = node.get("children") or []
            if children:
                for child in children:
                    visit(child, visiting)
                return
            parts = ref.strip("#/").split("/")
            collection = parts[0] if parts else ""
            if collection in {"texts", "tables", "pictures"}:
                emit_item(node, collection, ref)
        finally:
            visiting.discard(ref)

    # Furniture first (page decorations), then the body flow (§3.1 order).
    furniture = payload.get("furniture")
    if isinstance(furniture, dict):
        for child in furniture.get("children") or []:
            visit(child, set())
    body = payload.get("body")
    if isinstance(body, dict):
        for child in body.get("children") or []:
            visit(child, set())

    orphan_count, numeric_total = count_orphan_numeric_cells(tables_ir)
    orphan_ratio = orphan_count / numeric_total if numeric_total else 0.0

    confidence = max(0.0, 1.0 - dangling_refs * _CONFIDENCE_PER_DANGLING_REF)
    if not elements:
        hard_failures.append("no readable content extracted")

    quality = LayoutQualityReport(
        replacement_character_count=replacement_total,
        broken_unicode_count=broken_unicode_total,
        table_count=len(tables_ir),
        malformed_table_count=malformed_tables,
        orphan_numeric_ratio=orphan_ratio,
        repeated_header_footer_ratio=0.0,
        reading_order_confidence=confidence,
        warnings=[*warnings],
        hard_failures=hard_failures,
    )

    return DocumentIR(
        document_id=document_id,
        title=title,
        parser=manifest,
        pages=sorted(pages_by_number.values(), key=lambda p: p.physical_page),
        elements=elements,
        quality=quality,
        metadata={"layout_parser": "docling"},
    )


class DoclingParser:
    """V2 DocumentParser implementation over docling (spec §7.2)."""

    def __init__(
        self,
        *,
        parser_version: str | None = None,
        model_ids: dict[str, str] | None = None,
        model_revisions: dict[str, str] | None = None,
        options: dict[str, bool | int | float | str] | None = None,
        ocr: bool = False,
        table_structure: bool = True,
        formula_enrichment: bool = True,
        device: str = "cpu",
        artifacts_path: str = "",
    ) -> None:
        self._override_version = parser_version
        self._model_ids = model_ids or {}
        self._model_revisions = model_revisions or {}
        self._options = options or {
            "ocr": ocr,
            "table_structure": table_structure,
            "formula_enrichment": formula_enrichment,
            "device": device,
        }
        self._ocr = ocr
        self._table_structure = table_structure
        self._formula_enrichment = formula_enrichment
        self._device = device
        self._artifacts_path = artifacts_path
        self._manifest: ParserManifest | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> DoclingParser:
        return cls(
            ocr=bool(getattr(settings, "docling_ocr", False)),
            table_structure=bool(getattr(settings, "docling_table_structure", True)),
            formula_enrichment=bool(getattr(settings, "docling_formula_enrichment", True)),
            device=str(getattr(settings, "docling_device", "cpu")),
            artifacts_path=str(getattr(settings, "docling_artifacts_path", "")),
            model_ids={
                "layout": str(getattr(settings, "docling_layout_model", "")),
                "table": str(getattr(settings, "docling_table_model", "")),
            },
            model_revisions={
                "layout": str(getattr(settings, "docling_layout_revision", "")),
                "table": str(getattr(settings, "docling_table_revision", "")),
            },
        )

    @property
    def manifest(self) -> ParserManifest:
        if self._manifest is None:
            version = self._override_version or _installed_docling_version()
            self._manifest = ParserManifest(
                parser_id="docling",
                parser_version=version,
                model_ids=dict(self._model_ids),
                model_revisions=dict(self._model_revisions),
                options=dict(self._options),
                signature=compute_parser_signature(
                    parser_id="docling",
                    parser_version=version,
                    model_ids=self._model_ids,
                    model_revisions=self._model_revisions,
                    options=self._options,
                ),
            )
        return self._manifest

    def parse(self, path: Path, *, document_id: UUID, title: str | None = None) -> DocumentIR:
        payload = load_docling_payload(
            path,
            ocr=self._ocr,
            table_structure=self._table_structure,
            formula_enrichment=self._formula_enrichment,
            device=self._device,
            artifacts_path=self._artifacts_path,
        )
        check_docling_content(payload)
        resolved_title = title or _derive_title(payload) or path.stem
        return build_document_ir_from_docling(
            payload,
            document_id=document_id,
            title=resolved_title,
            manifest=self.manifest,
        )


def _installed_docling_version() -> str:
    try:
        import docling

        return str(docling.__version__)
    except ImportError as exc:
        raise ParseError(
            "docling is not installed; install the 'pdf-layout' extra",
            code=PDF_PARSER_UNAVAILABLE,
        ) from exc


def _derive_title(payload: dict[str, Any]) -> str:
    texts = [item for item in payload.get("texts") or [] if isinstance(item, dict)]
    for item in texts:
        if str(item.get("label", "")) == "title":
            return str(item.get("text", ""))[:300]
    for item in texts:
        text = str(item.get("text", "")).strip()
        if text:
            return text[:300]
    return ""

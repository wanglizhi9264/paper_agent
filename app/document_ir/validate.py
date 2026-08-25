"""Document IR validator enforcing the §5.3 invariants.

Structural checks are parser-independent: page sequence, reading order,
cross-references, provenance, bbox bounds, parent cycles, table cell
coverage/overlap, deterministic Markdown, NUL-free text, activation gate on
``hard_failures``, and parser/model revision requirements.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.document_ir import markdown as md
from app.document_ir.models import DocumentElement, DocumentIR, TableCell
from app.document_ir.serialize import manifest_signature

# Bbox may exceed page bounds by at most this many points (spec §5.3 #5).
BBOX_TOLERANCE = 0.5


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A single failed invariant with a stable machine-readable code."""

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ValidationResult:
    issues: list[ValidationIssue]

    @property
    def ok(self) -> bool:
        return not self.issues


def _check_pages(ir: DocumentIR) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    expected = list(range(1, len(ir.pages) + 1))
    actual = [page.physical_page for page in ir.pages]
    if actual != expected:
        issues.append(
            ValidationIssue(
                code="PAGE_SEQUENCE",
                message=f"pages must be continuous from 1; got {actual}",
            )
        )
    return issues


def _check_reading_order(elements: list[DocumentElement]) -> list[ValidationIssue]:
    orders = sorted(element.reading_order for element in elements)
    if orders != list(range(len(elements))):
        return [
            ValidationIssue(
                code="READING_ORDER",
                message="reading_order must be unique and continuous from 0",
            )
        ]
    return []


def _check_references(ir: DocumentIR) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    ids = [element.id for element in ir.elements]
    if len(set(ids)) != len(ids):
        issues.append(
            ValidationIssue(code="ELEMENT_ID_DUPLICATE", message="element ids must be unique")
        )
    known = set(ids)
    for page in ir.pages:
        for element_id in page.element_ids:
            if element_id not in known:
                issues.append(
                    ValidationIssue(
                        code="ELEMENT_REF_MISSING",
                        message=f"page {page.physical_page} references missing element {element_id}",
                    )
                )
    return issues


def _check_provenance(elements: list[DocumentElement]) -> list[ValidationIssue]:
    return [
        ValidationIssue(
            code="PROVENANCE_MISSING",
            message=f"element {element.id} has no SourceSpan",
        )
        for element in elements
        if not element.provenance
    ]


def _check_bbox_bounds(ir: DocumentIR) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    pages_by_number = {page.physical_page: page for page in ir.pages}
    for element in ir.elements:
        for span in element.provenance:
            if span.bbox is None:
                continue
            page = pages_by_number.get(span.physical_page)
            if page is None:
                continue
            bbox = span.bbox
            out_of_bounds = (
                bbox.x0 < -BBOX_TOLERANCE
                or bbox.y0 < -BBOX_TOLERANCE
                or bbox.x1 > page.width + BBOX_TOLERANCE
                or bbox.y1 > page.height + BBOX_TOLERANCE
            )
            if out_of_bounds:
                issues.append(
                    ValidationIssue(
                        code="BBOX_OUT_OF_BOUNDS",
                        message=(
                            f"element {element.id} bbox {bbox.model_dump_json()} exceeds "
                            f"page {span.physical_page} bounds"
                        ),
                    )
                )
    return issues


def _check_parents(elements: list[DocumentElement]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    by_id = {element.id: element for element in elements}
    for element in elements:
        current: UUID | None = element.parent_id
        seen: set[UUID] = set()
        while current is not None:
            if current not in by_id:
                issues.append(
                    ValidationIssue(
                        code="PARENT_MISSING",
                        message=f"element {element.id} references missing parent {current}",
                    )
                )
                break
            if current in seen or current == element.id:
                issues.append(
                    ValidationIssue(
                        code="PARENT_CYCLE",
                        message=f"parent chain from element {element.id} contains a cycle",
                    )
                )
                break
            seen.add(current)
            current = by_id[current].parent_id
    return issues


def _cell_positions(cell: TableCell) -> list[tuple[int, int]]:
    return [
        (r, c)
        for r in range(cell.row, cell.row + cell.row_span)
        for c in range(cell.column, cell.column + cell.column_span)
    ]


def _check_table_element(element: DocumentElement) -> list[ValidationIssue]:
    assert element.table is not None
    table = element.table
    issues: list[ValidationIssue] = []

    for row in table.header_rows:
        if not 0 <= row < table.row_count:
            issues.append(
                ValidationIssue(
                    code="TABLE_HEADER_ROW_INVALID",
                    message=f"header row {row} outside 0..{table.row_count - 1}",
                )
            )

    owner: dict[tuple[int, int], UUID] = {}
    seen_cell_ids: set[UUID] = set()
    for cell in table.cells:
        if cell.id in seen_cell_ids:
            issues.append(
                ValidationIssue(
                    code="TABLE_CELL_ID_DUPLICATE",
                    message=f"cell id {cell.id} appears twice",
                )
            )
        seen_cell_ids.add(cell.id)
        for position in _cell_positions(cell):
            r, c = position
            if r >= table.row_count or c >= table.column_count:
                issues.append(
                    ValidationIssue(
                        code="TABLE_CELL_OUT_OF_BOUNDS",
                        message=(
                            f"cell {cell.id} covers ({r},{c}) outside "
                            f"{table.row_count}x{table.column_count}"
                        ),
                    )
                )
                continue
            previous = owner.get(position)
            if previous is not None and previous != cell.id:
                issues.append(
                    ValidationIssue(
                        code="TABLE_OVERLAP",
                        message=(
                            f"cells {previous} and {cell.id} both cover logical "
                            f"coordinate ({r},{c})"
                        ),
                    )
                )
            owner[position] = cell.id

    regenerated = md.render_table_markdown(
        table.cells,
        row_count=table.row_count,
        column_count=table.column_count,
        header_rows=table.header_rows,
    )
    if regenerated != table.markdown:
        issues.append(
            ValidationIssue(
                code="TABLE_MARKDOWN_MISMATCH",
                message=f"table {element.id} markdown is not the canonical rendering of cells",
            )
        )
    return issues


def _check_text_nul(ir: DocumentIR) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if "\x00" in ir.title:
        issues.append(ValidationIssue(code="TEXT_NUL", message="title contains NUL"))
    for element in ir.elements:
        texts = (element.raw_text, element.normalized_text)
        if any("\x00" in text for text in texts):
            issues.append(
                ValidationIssue(
                    code="TEXT_NUL",
                    message=f"element {element.id} text contains NUL",
                )
            )
        if element.table is not None:
            for cell in element.table.cells:
                if any("\x00" in text for text in (cell.raw_text, cell.normalized_text)):
                    issues.append(
                        ValidationIssue(
                            code="TEXT_NUL",
                            message=f"cell {cell.id} text contains NUL",
                        )
                    )
    return issues


def _check_parser(ir: DocumentIR) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    manifest = ir.parser
    if not manifest.parser_version.strip():
        issues.append(
            ValidationIssue(code="REVISION_EMPTY", message="parser_version must be non-empty")
        )
    for role, revision in manifest.model_revisions.items():
        if not revision.strip() or revision.strip().lower() == "unknown":
            issues.append(
                ValidationIssue(
                    code="REVISION_UNKNOWN",
                    message=f"model revision for '{role}' must be a pinned value",
                )
            )
    expected_signature = manifest_signature(manifest)
    if expected_signature != manifest.signature:
        issues.append(
            ValidationIssue(
                code="SIGNATURE_MISMATCH",
                message="parser signature does not match its manifest fields",
            )
        )
    return issues


def validate_document_ir(ir: DocumentIR) -> ValidationResult:
    """Check every applicable §5.3 invariant and return all failures."""
    issues: list[ValidationIssue] = []
    issues.extend(_check_pages(ir))
    issues.extend(_check_reading_order(ir.elements))
    issues.extend(_check_references(ir))
    issues.extend(_check_provenance(ir.elements))
    issues.extend(_check_bbox_bounds(ir))
    issues.extend(_check_parents(ir.elements))
    for element in ir.elements:
        if element.table is not None:
            issues.extend(_check_table_element(element))
    issues.extend(_check_text_nul(ir))
    issues.extend(_check_parser(ir))
    if ir.quality.hard_failures:
        issues.append(
            ValidationIssue(
                code="HARD_FAILURES_PRESENT",
                message="quality.hard_failures must be empty for activation",
            )
        )
    return ValidationResult(issues=issues)

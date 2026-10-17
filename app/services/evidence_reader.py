from __future__ import annotations

import asyncio
from typing import Any

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import truncate_text
from app.agent.contracts import AgentError, Evidence, Observation, ReadTable, TokenCounter
from app.core.config import Settings
from app.models.chunk import Chunk, DocumentVersion
from app.models.document import Document
from app.schemas.chat import BBoxOut, SourceOut


class TableReader:
    def __init__(self, pins: dict[str, Any], settings: Settings, counter: TokenCounter) -> None:
        self.pins = pins
        self.settings = settings
        self.counter = counter

    async def table_outline(
        self, db: AsyncSession, chunk: Chunk, *, label_limit: int = 12
    ) -> JsonValue:
        from app.services.ir_artifacts import IRArtifactManager

        version = await db.get(DocumentVersion, chunk.document_version_id)
        if not version or not version.ir_path or not version.ir_sha256:
            return None
        ir = await asyncio.to_thread(
            IRArtifactManager(self.settings.storage_dir).verify, version.ir_path, version.ir_sha256
        )
        element_id = (chunk.metadata_ or {}).get("element_id")
        element = next((e for e in ir.elements if str(e.id) == str(element_id) and e.table), None)
        if element is None or element.table is None:
            return None
        table = element.table
        rows: list[JsonValue] = []
        for row in range(min(table.row_count, 20)):
            labels = [
                c.raw_text
                for c in table.cells
                if c.is_row_header and c.row <= row < c.row + c.row_span
            ]
            if labels:
                rows.append(
                    {
                        "row": row,
                        "label": truncate_text(" > ".join(labels), label_limit, self.counter),
                    }
                )
        return {
            "element_id": str(element.id),
            "row_count": table.row_count,
            "column_count": table.column_count,
            "max_rows_per_read": max(1, 40 // table.column_count - 1),
            "rows": rows,
            "truncated": table.row_count > 20,
        }

    async def read_table(self, db: AsyncSession, args: ReadTable) -> Observation:
        from app.services.ir_artifacts import IRArtifactManager

        chunk = await db.get(Chunk, args.evidence_id)
        if chunk is None or self.pins["documents"].get(str(chunk.document_id)) != str(
            chunk.document_version_id
        ):
            raise AgentError("SCOPE_VIOLATION")
        version = await db.get(DocumentVersion, chunk.document_version_id)
        doc = await db.get(Document, chunk.document_id)
        if not version or not version.ir_path or not version.ir_sha256 or not doc:
            raise AgentError("TABLE_STRUCTURE_UNAVAILABLE")
        ir = await asyncio.to_thread(
            IRArtifactManager(self.settings.storage_dir).verify, version.ir_path, version.ir_sha256
        )
        element_id = (chunk.metadata_ or {}).get("element_id")
        element = next((e for e in ir.elements if str(e.id) == str(element_id) and e.table), None)
        if element is None or element.table is None:
            raise AgentError("TABLE_STRUCTURE_UNAVAILABLE")
        selected = [
            c
            for c in element.table.cells
            if (not args.cell_ids and not args.rows) or c.id in args.cell_ids or c.row in args.rows
        ]
        if args.cell_ids and not set(args.cell_ids).issubset(c.id for c in element.table.cells):
            raise AgentError("TOOL_ARGUMENT_INVALID")
        if args.rows and any(r < 0 or r >= element.table.row_count for r in args.rows):
            raise AgentError("TOOL_ARGUMENT_INVALID")
        selected_rows = {c.row for c in selected}
        headers = [
            c
            for c in element.table.cells
            if c.is_column_header
            or (c.is_row_header and any(c.row <= row < c.row + c.row_span for row in selected_rows))
        ]
        cells = list({c.id: c for c in [*selected, *headers]}.values())
        if len(cells) > 40:
            return Observation(
                tool="read_table",
                status="error",
                error_code="TABLE_SELECTION_TOO_LARGE",
                data={
                    "max_cells": 40,
                    "selected_cells": len(cells),
                    "hint": "Select fewer relevant rows; include at most max_rows_per_read from table_outline.",
                },
            )
        text = "\n".join(f"row={c.row},column={c.column}: {c.raw_text}" for c in cells)
        spans = [p for c in cells for p in c.provenance if p.bbox]
        if not spans:
            raise AgentError("TABLE_STRUCTURE_UNAVAILABLE")
        source = SourceOut(
            index=1,
            chunk_id=chunk.id,
            document_id=doc.id,
            document_title=doc.title or doc.filename,
            section_path=chunk.section_path,
            page=str(spans[0].physical_page),
            page_start=min(p.physical_page for p in spans),
            page_end=max(p.physical_page for p in spans),
            element_id=element.id,
            element_kind="table",
            cell_ids=[c.id for c in cells],
            bboxes=[
                BBoxOut(physical_page=p.physical_page, **p.bbox.model_dump())
                for p in spans
                if p.bbox
            ],
            content=text,
            truncated=False,
        )
        evidence = Evidence(
            source=source, document_version_id=version.id, content_hash=chunk.content_hash
        )
        # Tables need surrounding method/metric definitions. Keep each paragraph's own
        # identity rather than hiding expanded prose behind the table citation.
        page_chunks = list(
            (
                await db.execute(
                    select(Chunk)
                    .where(
                        Chunk.document_version_id == version.id,
                        Chunk.page_start >= source.page_start,
                        Chunk.page_start <= source.page_end,
                    )
                    .order_by(Chunk.chunk_index)
                )
            ).scalars()
        )
        table_positions = [
            c.chunk_index
            for c in page_chunks
            if (c.metadata_ or {}).get("element_id") == str(element.id)
        ]
        context_evidence: list[Evidence] = []
        context_data: list[JsonValue] = []
        if table_positions:
            prose = [
                c
                for c in page_chunks
                if (c.metadata_ or {}).get("element_kind") in {"paragraph", "caption"}
            ]
            before = [c for c in prose if c.chunk_index < min(table_positions)][-1:]
            after = [c for c in prose if c.chunk_index > max(table_positions)][:2]
            for neighbor in [*before, *after]:
                content = truncate_text(neighbor.raw_content, 400, self.counter)
                metadata = neighbor.metadata_ or {}
                neighbor_source = SourceOut(
                    index=1,
                    chunk_id=neighbor.id,
                    document_id=doc.id,
                    document_title=doc.title or doc.filename,
                    section_path=neighbor.section_path,
                    page=str(neighbor.page_start or "unknown"),
                    page_start=neighbor.page_start,
                    page_end=neighbor.page_end,
                    element_id=metadata.get("element_id"),
                    element_kind=metadata.get("element_kind"),
                    cell_ids=[],
                    bboxes=[BBoxOut.model_validate(b) for b in metadata.get("bboxes", [])],
                    content=content,
                    truncated=content != neighbor.raw_content,
                )
                context_evidence.append(
                    Evidence(
                        source=neighbor_source,
                        document_version_id=version.id,
                        content_hash=neighbor.content_hash,
                    )
                )
                context_data.append(
                    {
                        "chunk_id": str(neighbor.id),
                        "text": truncate_text(content, 120, self.counter),
                    }
                )
        return Observation(
            tool="read_table",
            status="ok",
            data={"chunk_id": str(chunk.id), "text": text, "context": context_data},
            evidence=[evidence, *context_evidence],
        )

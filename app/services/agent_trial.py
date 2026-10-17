from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import JsonValue, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.context import truncate_text
from app.agent.contracts import (
    Action,
    AgentError,
    Evidence,
    ListPapers,
    Observation,
    ReadChunks,
    ReadTable,
    RuntimeState,
    SearchPapers,
    TokenCounter,
)
from app.api.errors import AppError
from app.core.config import Settings
from app.document_ir.errors import ParseError
from app.embedding.base import EmbeddingError, EmbeddingProvider
from app.models.agent_run import AgentTrialRun
from app.models.chunk import Chunk, DocumentVersion
from app.models.collection import CollectionDocument
from app.models.document import Document
from app.models.enums import DocumentStatus, MessageRole, MessageStatus
from app.models.index_snapshot import IndexSnapshot, SystemState
from app.models.session import Message, Session
from app.schemas.chat import BBoxOut, SourceOut
from app.schemas.search import SearchRequest, SearchScope
from app.services.retrieval import search_corpus


async def corpus_pins(db: AsyncSession, scope: SearchScope) -> dict[str, Any]:
    state = await db.get(SystemState, 1)
    if state is None or state.active_index_snapshot_id is None:
        raise AgentError("SNAPSHOT_UNAVAILABLE")
    query = select(Document).where(Document.status == DocumentStatus.READY)
    if scope.type == "documents":
        query = query.where(Document.id.in_(scope.document_ids))
    elif scope.type == "collection":
        query = query.join(CollectionDocument).where(
            CollectionDocument.collection_id == scope.collection_id
        )
    docs = list((await db.execute(query.order_by(Document.id))).scalars())
    return {
        "snapshot_id": str(state.active_index_snapshot_id),
        "scope": scope.model_dump(mode="json"),
        "documents": {str(d.id): str(d.active_document_version_id) for d in docs},
    }


class CorpusTools:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        pins: dict[str, Any],
        embedding: EmbeddingProvider,
        settings: Settings,
        counter: TokenCounter,
        pinned: bool = False,
    ) -> None:
        self.sessions = sessions
        self.pins = pins
        self.embedding = embedding
        self.settings = settings
        self.counter = counter
        self.pinned = pinned

    async def check(self, db: AsyncSession, *, lock: bool = False) -> None:
        if self.pinned:
            if await db.get(IndexSnapshot, uuid.UUID(self.pins["snapshot_id"])) is None:
                raise AgentError("SNAPSHOT_UNAVAILABLE")
            query = select(Document).where(
                Document.id.in_([uuid.UUID(k) for k in self.pins["documents"]])
            )
            if lock:
                query = query.with_for_update(read=True)
            docs = list((await db.execute(query)).scalars())
            if len(docs) != len(self.pins["documents"]) or any(
                d.status in {"deleting", "deleted"} for d in docs
            ):
                raise AgentError("SOURCE_INVALIDATED")
            versions = list(
                (
                    await db.execute(
                        select(DocumentVersion.id).where(
                            DocumentVersion.id.in_(
                                [uuid.UUID(v) for v in self.pins["documents"].values()]
                            )
                        )
                    )
                ).scalars()
            )
            if len(versions) != len(self.pins["documents"]):
                raise AgentError("SOURCE_INVALIDATED")
            return
        current = await corpus_pins(db, SearchScope.model_validate(self.pins["scope"]))
        if current != {k: self.pins[k] for k in ("snapshot_id", "scope", "documents")}:
            raise AgentError("SOURCE_INVALIDATED")

    async def execute(self, action: Action) -> Observation:
        try:
            return await self._execute_impl(action)
        except (AppError, EmbeddingError, ParseError) as exc:
            raise AgentError(exc.code) from exc
        except ValidationError as exc:
            raise AgentError("TOOL_OUTPUT_INVALID") from exc

    async def _execute_impl(self, action: Action) -> Observation:
        async with self.sessions() as db:
            await self.check(db)
            if action.tool == "read_table":
                return await self.read_table(db, ReadTable.model_validate(action.arguments))
            if action.tool == "list_papers":
                args = ListPapers.model_validate(action.arguments)
                docs = list(
                    (
                        await db.execute(
                            select(Document)
                            .where(
                                Document.id.in_([uuid.UUID(key) for key in self.pins["documents"]])
                            )
                            .order_by(Document.id)
                        )
                    ).scalars()
                )
                paper_data: list[JsonValue] = []
                terms = re.findall(r"\w+", args.query.casefold())
                for doc in docs:
                    title = (doc.title or doc.filename).casefold()
                    if not terms or any(term in title for term in terms):
                        paper_data.append(
                            {
                                "document_id": str(doc.id),
                                "title": truncate_text(doc.title or doc.filename, 60, self.counter),
                            }
                        )
                return Observation(tool=action.tool, status="ok", data=paper_data[: args.limit])
            if action.tool == "search_papers":
                search = SearchPapers.model_validate(action.arguments)
                if self.counter.count(search.query) > 800:
                    raise AgentError("TOOL_ARGUMENT_INVALID")
                ids = search.document_ids or [uuid.UUID(key) for key in self.pins["documents"]]
                if not {str(i) for i in ids}.issubset(self.pins["documents"]):
                    raise AgentError("SCOPE_VIOLATION")
                if not ids:
                    return Observation(tool=action.tool, status="ok", data=[])
                response = await search_corpus(
                    db,
                    SearchRequest(
                        query=search.query,
                        scope=SearchScope(type="documents", document_ids=ids),
                        top_k=search.top_k,
                    ),
                    self.embedding,
                    settings=self.settings,
                    pinned_snapshot_id=uuid.UUID(self.pins["snapshot_id"]) if self.pinned else None,
                    pinned_versions={
                        uuid.UUID(k): uuid.UUID(v) for k, v in self.pins["documents"].items()
                    }
                    if self.pinned
                    else None,
                )
                chunk_ids = [r.chunk_id for r in response.results]
                limit = 160
            elif action.tool == "read_chunks":
                chunk_ids = ReadChunks.model_validate(action.arguments).chunk_ids
                limit = 400
            else:
                raise AgentError("TOOL_NOT_ALLOWED")
            rows = list(
                (
                    await db.execute(
                        select(Chunk, Document)
                        .join(Document, Document.id == Chunk.document_id)
                        .where(Chunk.id.in_(chunk_ids))
                    )
                ).all()
            )
            by_id = {c.id: (c, d) for c, d in rows}
            if len(by_id) != len(set(chunk_ids)):
                raise AgentError("SCOPE_VIOLATION")
            evidence = []
            data: list[JsonValue] = []
            outlined: set[str] = set()
            for chunk_id in chunk_ids:
                chunk, doc = by_id[chunk_id]
                if self.pins["documents"].get(str(doc.id)) != str(chunk.document_version_id):
                    raise AgentError("SCOPE_VIOLATION")
                metadata = chunk.metadata_ or {}
                content = truncate_text(chunk.raw_content, limit, self.counter)
                source = SourceOut(
                    index=1,
                    chunk_id=chunk.id,
                    document_id=doc.id,
                    document_title=doc.title or doc.filename,
                    section_path=chunk.section_path,
                    page=str(chunk.page_start or "unknown"),
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    element_id=metadata.get("element_id"),
                    element_kind=(
                        "table_raw_text"
                        if metadata.get("chunk_subtype") == "table_raw_text"
                        and not metadata.get("cell_ids")
                        else metadata.get("element_kind")
                    ),
                    cell_ids=metadata.get("cell_ids", []),
                    bboxes=[BBoxOut.model_validate(b) for b in metadata.get("bboxes", [])],
                    content=content,
                    truncated=content != chunk.raw_content,
                )
                evidence.append(
                    Evidence(
                        source=source,
                        document_version_id=chunk.document_version_id,
                        content_hash=chunk.content_hash,
                    )
                )
                item: dict[str, JsonValue] = {
                    "chunk_id": str(chunk.id),
                    "document_id": str(doc.id),
                    "title": truncate_text(source.document_title, 40, self.counter),
                    "page": source.page,
                    "text": content,
                    "element_kind": source.element_kind,
                    "truncated": source.truncated,
                }
                if source.element_kind == "table" and source.element_id:
                    identity = str(source.element_id)
                    if identity not in outlined:
                        outline = await self.table_outline(db, chunk)
                        if outline is not None:
                            item["table_outline"] = outline
                            outlined.add(identity)
                data.append(item)
            # Recheck after retrieval, so a concurrent activation cannot commit mixed evidence.
            await db.rollback()
            await self.check(db)
            return Observation(tool=action.tool, status="ok", data=data, evidence=evidence)

    async def table_outline(self, db: AsyncSession, chunk: Chunk) -> JsonValue:
        from app.services.ir_artifacts import IRArtifactManager

        version = await db.get(DocumentVersion, chunk.document_version_id)
        if not version or not version.ir_path or not version.ir_sha256:
            return None
        ir = IRArtifactManager(self.settings.storage_dir).verify(version.ir_path, version.ir_sha256)
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
                    {"row": row, "label": truncate_text(" > ".join(labels), 12, self.counter)}
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
        ir = IRArtifactManager(self.settings.storage_dir).verify(version.ir_path, version.ir_sha256)
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


class TrialStore:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        run_id: uuid.UUID,
        tools: CorpusTools,
    ) -> None:
        self.sessions = sessions
        self.run_id = run_id
        self.tools = tools

    async def check(self) -> None:
        async with self.sessions() as db:
            await self.tools.check(db)

    async def save(self, state: RuntimeState, event: str) -> None:
        async with self.sessions() as db, db.begin():
            run = await db.get(AgentTrialRun, self.run_id, with_for_update=True)
            if run is None:
                raise AgentError("RUN_NOT_FOUND")
            if run.status in {"succeeded", "failed"}:
                raise AgentError("RUN_TERMINAL")
            await self.tools.check(db)
            run.state = state.model_dump(mode="json")
            run.events = [
                *run.events,
                {
                    "sequence": len(run.events) + 1,
                    "type": event,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "tool": state.action.tool if state.action else None,
                    "llm_calls": state.llm_calls,
                    "tool_calls": state.tool_calls,
                },
            ]
            if state.phase == "done":
                message = Message(
                    session_id=run.session_id,
                    role=MessageRole.ASSISTANT,
                    status=MessageStatus.COMPLETE,
                    content=state.answer or "",
                    citations=state.citations,
                )
                db.add(message)
                await db.flush()
                run.final_message_id = message.id
                run.status = "succeeded"
            else:
                run.status = "running"
        print(f"  {event} (LLM {state.llm_calls}/8, tools {state.tool_calls}/10)", flush=True)

    async def mark_error(self, code: str, *, interrupted: bool = False) -> None:
        async with self.sessions() as db, db.begin():
            run = await db.get(AgentTrialRun, self.run_id, with_for_update=True)
            if run is not None and run.status not in {"succeeded", "failed"}:
                run.status = "interrupted" if interrupted else "failed"
                run.error_code = code


async def create_trial(
    db: AsyncSession,
    session: Session,
    query: str,
    pins: dict[str, Any],
    skills: dict[str, str],
) -> AgentTrialRun:
    from app.models.agent import AgentRun

    await db.get(Session, session.id, with_for_update=True)
    active = await db.scalar(
        select(AgentRun.id).where(
            AgentRun.session_id == session.id,
            AgentRun.status.in_(["queued", "running", "interrupted"]),
        )
    )
    if active:
        raise AgentError("SESSION_RUN_ACTIVE")
    run = AgentTrialRun(
        session_id=session.id,
        query=query,
        pins=pins,
        status="running",
        state=RuntimeState(loaded_skills=skills).model_dump(mode="json"),
        events=[],
        deadline=datetime.now(UTC) + timedelta(seconds=180),
    )
    db.add(Message(session_id=session.id, role=MessageRole.USER, content=query))
    db.add(run)
    await db.flush()
    return run

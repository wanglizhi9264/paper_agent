from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.contracts import AgentError, ReadTable
from app.api.errors import ConflictError, NotFoundError, PayloadTooLargeError
from app.core.config import Settings
from app.document_ir.errors import ParseError
from app.models.chunk import Chunk, DocumentVersion
from app.models.document import Document
from app.models.evidence import EvidenceContext, EvidenceOperation
from app.models.index_snapshot import IndexSnapshot
from app.schemas.evidence import (
    EvidenceChunks,
    EvidenceOperationOut,
    EvidenceResponse,
    EvidenceResult,
    EvidenceSearch,
    EvidenceTable,
    PaperItem,
    PaperList,
    PaperListRequest,
)
from app.schemas.search import SearchScope
from app.services.agent_trial import corpus_pins
from app.services.evidence_reader import TableReader

TERMINAL = {"succeeded", "failed", "cancelled"}


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


async def lifecycle_lock(db: AsyncSession) -> None:
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(731946218006)"))


def bounded[T](value: T) -> T:
    # Pydantic models only; keep the size policy independent of any LLM tokenizer.
    from pydantic import BaseModel

    if isinstance(value, BaseModel) and len(value.model_dump_json().encode("utf-8")) > 65536:
        raise PayloadTooLargeError(
            code="TOOL_OUTPUT_TOO_LARGE", message="Select fewer results or cells."
        )
    return value


async def list_papers(db: AsyncSession, body: PaperListRequest) -> PaperList:
    from app.models.collection import CollectionDocument

    stmt = select(Document).where(Document.status == "ready")
    if body.scope.type == "documents":
        stmt = stmt.where(Document.id.in_(body.scope.document_ids))
    elif body.scope.type == "collection":
        stmt = stmt.join(CollectionDocument).where(
            CollectionDocument.collection_id == body.scope.collection_id
        )
    if body.cursor:
        stmt = stmt.where(Document.id > body.cursor)
    if body.query:
        # Escape wildcard syntax; title search does not alter the fixed scope.
        needle = (
            "%" + body.query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
        stmt = stmt.where(
            Document.title.ilike(needle, escape="\\") | Document.filename.ilike(needle, escape="\\")
        )
    rows = list((await db.execute(stmt.order_by(Document.id).limit(body.limit + 1))).scalars())
    return bounded(
        PaperList(
            items=[
                PaperItem(
                    document_id=d.id,
                    title=d.title or d.filename,
                    filename=d.filename,
                    page_count=d.page_count,
                )
                for d in rows[: body.limit]
            ],
            next_cursor=rows[body.limit - 1].id if len(rows) > body.limit else None,
        )
    )


async def context(
    db: AsyncSession, context_id: uuid.UUID, *, lock_documents: bool = False
) -> EvidenceContext:
    item = await db.scalar(
        select(EvidenceContext).where(EvidenceContext.id == context_id).with_for_update(read=True)
    )
    if item is None:
        raise NotFoundError(code="CONTEXT_NOT_FOUND")
    if item.status != "active" or utc(item.expires_at) <= datetime.now(UTC):
        raise ConflictError(
            code="CONTEXT_EXPIRED"
            if item.status == "expired" or utc(item.expires_at) <= datetime.now(UTC)
            else "SOURCE_INVALIDATED"
        )
    docs = list(
        (
            await db.execute(
                select(Document)
                .where(Document.id.in_([uuid.UUID(k) for k in item.pins["documents"]]))
                .order_by(Document.id)
                .with_for_update(read=True)
            )
        ).scalars()
    )
    if len(docs) != len(item.pins["documents"]) or any(
        d.status in {"deleting", "deleted"} for d in docs
    ):
        raise ConflictError(code="SOURCE_INVALIDATED")
    if item.snapshot_id is None or await db.get(IndexSnapshot, item.snapshot_id) is None:
        raise ConflictError(code="SNAPSHOT_UNAVAILABLE")
    versions = list(
        (
            await db.execute(
                select(DocumentVersion.id).where(
                    DocumentVersion.id.in_([uuid.UUID(v) for v in item.pins["documents"].values()])
                )
            )
        ).scalars()
    )
    if len(versions) != len(item.pins["documents"]):
        raise ConflictError(code="SOURCE_INVALIDATED")
    return item


async def create_search(db: AsyncSession, body: EvidenceSearch, key: str) -> EvidenceOperation:
    if not 1 <= len(key) <= 128:
        raise ConflictError(code="IDEMPOTENCY_KEY_INVALID")
    await lifecycle_lock(db)
    digest = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    prior = await db.scalar(
        select(EvidenceOperation).where(EvidenceOperation.idempotency_key == key)
    )
    if prior:
        if prior.request_hash != digest:
            raise ConflictError(code="IDEMPOTENCY_CONFLICT")
        await context(db, prior.context_id)
        return prior
    now = datetime.now(UTC)
    if body.context_id:
        item = await context(db, body.context_id, lock_documents=True)
    else:
        scope = body.scope or SearchScope(type="all")
        pins = await corpus_pins(db, scope)
        if not pins["documents"]:
            raise ConflictError(code="SCOPE_EMPTY")
        snapshot = await db.get(IndexSnapshot, uuid.UUID(pins["snapshot_id"]))
        manifest_versions = (
            (snapshot.manifest or {}).get("document_versions", {}) if snapshot else {}
        )
        if any(
            manifest_versions.get(doc_id) != version
            for doc_id, version in pins["documents"].items()
        ):
            raise ConflictError(
                code="SOURCE_INVALIDATED", message="Index activation changed; retry the search."
            )
        item = EvidenceContext(
            id=uuid.uuid4(),
            snapshot_id=uuid.UUID(pins["snapshot_id"]),
            pins=pins,
            status="active",
            created_at=now,
            expires_at=now + timedelta(minutes=30),
        )
        db.add(item)
        await db.flush()
        await context(db, item.id, lock_documents=True)
    op = EvidenceOperation(
        id=uuid.uuid4(),
        context_id=item.id,
        idempotency_key=key,
        request_hash=digest,
        request=body.model_dump(mode="json"),
        status="queued",
        attempts=0,
        fencing_token=0,
        created_at=now,
        deadline=now + timedelta(seconds=75),
    )
    db.add(op)
    await db.flush()
    return op


def operation_out(op: EvidenceOperation, *, include_result: bool = True) -> EvidenceOperationOut:
    return EvidenceOperationOut.model_validate(
        {
            "operation_id": op.id,
            "context_id": op.context_id,
            "status": op.status,
            "error_code": op.error_code,
            "result": op.result if include_result else None,
        }
    )


async def get_operation(
    db: AsyncSession, operation_id: uuid.UUID, *, cancel: bool = False
) -> EvidenceOperation:
    existing = await db.get(EvidenceOperation, operation_id)
    if existing is None:
        raise NotFoundError(code="OPERATION_NOT_FOUND")
    if not cancel:
        await context(db, existing.context_id, lock_documents=True)
        return existing
    # Use context -> operation lock order, matching worker completion and invalidation.
    await db.execute(
        select(EvidenceContext.id)
        .where(EvidenceContext.id == existing.context_id)
        .with_for_update(read=True)
    )
    op = await db.scalar(
        select(EvidenceOperation)
        .where(EvidenceOperation.id == operation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if op is None:
        raise NotFoundError(code="OPERATION_NOT_FOUND")
    if op.status not in TERMINAL:
        op.status, op.finished_at, op.error_code = "cancelled", datetime.now(UTC), "CANCELLED"
        op.fencing_token += 1
        op.lease_expiry = None
    return op


async def read_chunks(db: AsyncSession, body: EvidenceChunks) -> EvidenceResponse:
    item = await context(db, body.context_id, lock_documents=True)
    ids = [r.chunk_id for r in body.requests]
    rows = list(
        (
            await db.execute(
                select(Chunk, Document)
                .join(Document, Document.id == Chunk.document_id)
                .where(Chunk.id.in_(ids))
            )
        ).all()
    )
    by_id = {c.id: (c, d) for c, d in rows}
    if set(by_id) != set(ids):
        raise NotFoundError(code="CHUNK_NOT_FOUND")
    results = []
    for request in body.requests:
        chunk, doc = by_id[request.chunk_id]
        if item.pins["documents"].get(str(doc.id)) != str(chunk.document_version_id):
            raise ConflictError(code="SCOPE_VIOLATION")
        if request.start_char > len(chunk.raw_content):
            raise ConflictError(code="CHUNK_OFFSET_INVALID")
        end = min(len(chunk.raw_content), request.start_char + request.max_chars)
        metadata = chunk.metadata_ or {}
        results.append(
            EvidenceResult(
                chunk_id=chunk.id,
                document_id=doc.id,
                document_version_id=chunk.document_version_id,
                document_title=doc.title or doc.filename,
                section_path=chunk.section_path,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
                raw_content=chunk.raw_content[request.start_char : end],
                content_hash=chunk.content_hash,
                element_id=metadata.get("element_id"),
                element_kind="table_raw_text"
                if metadata.get("chunk_subtype") == "table_raw_text"
                and not metadata.get("cell_ids")
                else metadata.get("element_kind"),
                cell_ids=metadata.get("cell_ids", []),
                bboxes=metadata.get("bboxes", []),
                start_char=request.start_char,
                truncated=request.start_char > 0 or end < len(chunk.raw_content),
                next_start_char=end if end < len(chunk.raw_content) else None,
            )
        )
    return response(item, results)


class CharacterCounter:
    """Reader-only excerpt bound, deliberately not advertised as LLM token counts."""

    signature = "evidence-character-bound-v1"

    def count(self, value: str) -> int:
        return len(value)

    def count_messages(self, messages: list[Any]) -> int:
        return sum(len(m.content) for m in messages)

    def count_request(self, messages: list[Any], tools: list[dict[str, object]]) -> int:
        return self.count_messages(messages) + len(json.dumps(tools))


async def read_table(db: AsyncSession, body: EvidenceTable, settings: Settings) -> EvidenceResponse:
    item = await context(db, body.context_id, lock_documents=True)
    reader = TableReader(item.pins, settings, CharacterCounter())
    try:
        result = await reader.read_table(
            db, ReadTable.model_validate(body.model_dump(exclude={"context_id"}))
        )
    except (AgentError, ParseError) as exc:
        raise ConflictError(code=exc.code) from exc
    if result.status == "error":
        raise ConflictError(
            code=result.error_code or "TABLE_READ_FAILED",
            details=result.data if isinstance(result.data, dict) else {},
        )
    return response(
        item,
        [
            EvidenceResult.from_source(e.source, e.document_version_id, e.content_hash)
            for e in result.evidence
        ],
    )


def response(
    item: EvidenceContext, results: list[EvidenceResult], degraded: list[str] | None = None
) -> EvidenceResponse:
    return bounded(
        EvidenceResponse(
            context_id=item.id,
            snapshot_id=uuid.UUID(item.pins["snapshot_id"]),
            expires_at=item.expires_at,
            results=results,
            degraded_reasons=degraded or [],
        )
    )


async def invalidate_document_contexts(db: AsyncSession, document_id: uuid.UUID) -> None:
    await lifecycle_lock(db)
    rows = list(
        (
            await db.execute(
                select(EvidenceContext).where(EvidenceContext.status == "active").with_for_update()
            )
        ).scalars()
    )
    for item in rows:
        if str(document_id) in item.pins["documents"]:
            await retire_context(db, item, "invalidated")


async def retire_context(db: AsyncSession, item: EvidenceContext, status: str) -> None:
    item.status, item.snapshot_id = status, None
    ops = list(
        (
            await db.execute(
                select(EvidenceOperation)
                .where(EvidenceOperation.context_id == item.id)
                .with_for_update()
            )
        ).scalars()
    )
    for op in ops:
        op.result = None
        if op.status not in TERMINAL:
            op.status, op.finished_at, op.error_code = (
                "cancelled",
                datetime.now(UTC),
                "SOURCE_INVALIDATED",
            )
            op.fencing_token += 1
            op.lease_expiry = None


async def cleanup_contexts(db: AsyncSession) -> None:
    now = datetime.now(UTC)
    rows = list(
        (
            await db.execute(
                select(EvidenceContext)
                .where(EvidenceContext.status == "active", EvidenceContext.expires_at <= now)
                .with_for_update()
            )
        ).scalars()
    )
    for item in rows:
        await retire_context(db, item, "expired")
    await db.execute(
        delete(EvidenceContext)
        .where(
            EvidenceContext.status != "active", EvidenceContext.expires_at < now - timedelta(days=1)
        )
        .execution_options(synchronize_session=False)
    )

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.agent.contracts import AgentError
from app.api.errors import AppError
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.embedding.base import EmbeddingError
from app.embedding.registry import get_embedding_provider
from app.models.evidence import EvidenceOperation
from app.schemas.evidence import ChunkRead, EvidenceChunks, EvidenceSearch
from app.schemas.search import SearchRequest, SearchScope
from app.services import evidence as service
from app.services.evidence_reader import TableReader
from app.services.retrieval import search_corpus

logger = get_logger(__name__)


async def evidence_search(ctx: dict[str, Any], operation_id: str) -> None:
    del ctx
    sessions = get_sessionmaker()
    token: int | None = None
    op_id = uuid.UUID(operation_id)
    try:
        async with sessions() as db, db.begin():
            existing = await db.get(EvidenceOperation, op_id)
            if existing is None:
                return
            item = await service.context(db, existing.context_id)
            op = await db.scalar(
                select(EvidenceOperation)
                .where(EvidenceOperation.id == op_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if op is None or op.status != "queued" or service.utc(op.deadline) <= datetime.now(UTC):
                return
            op.status, op.attempts = "running", op.attempts + 1
            op.fencing_token += 1
            token = op.fencing_token
            op.lease_expiry = datetime.now(UTC) + timedelta(seconds=30)
            pins, deadline = item.pins, service.utc(op.deadline)
            context_id = item.id
            request = EvidenceSearch.model_validate(op.request)

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(5)
                async with sessions() as db, db.begin():
                    row = await db.scalar(
                        select(EvidenceOperation)
                        .where(EvidenceOperation.id == op_id)
                        .with_for_update()
                    )
                    if (
                        row is None
                        or row.status != "running"
                        or row.fencing_token != token
                        or (row.lease_expiry and service.utc(row.lease_expiry) <= datetime.now(UTC))
                    ):
                        raise AgentError("OPERATION_CANCELLED")
                    row.lease_expiry = datetime.now(UTC) + timedelta(seconds=30)

        async def retrieve() -> dict[str, Any]:
            settings = get_settings()
            # Model construction happens in the worker, never the API/MCP adapter.
            embedding = await asyncio.to_thread(get_embedding_provider)
            async with sessions() as db:
                result = await search_corpus(
                    db,
                    SearchRequest(
                        query=request.query,
                        scope=SearchScope(
                            type="documents", document_ids=[uuid.UUID(k) for k in pins["documents"]]
                        ),
                        top_k=request.top_k,
                    ),
                    embedding,
                    settings=settings,
                    pinned_snapshot_id=uuid.UUID(pins["snapshot_id"]),
                    pinned_versions={
                        uuid.UUID(k): uuid.UUID(v) for k, v in pins["documents"].items()
                    },
                )
            async with sessions() as db, db.begin():
                item = await service.context(db, context_id, lock_documents=True)
                results = []
                for start in range(0, len(result.results), 4):
                    batch = await service.read_chunks(
                        db,
                        EvidenceChunks(
                            context_id=context_id,
                            requests=[
                                ChunkRead(chunk_id=r.chunk_id, max_chars=1600)
                                for r in result.results[start : start + 4]
                            ],
                        ),
                    )
                    results.extend(batch.results)
                reader = TableReader(pins, settings, service.CharacterCounter())
                outlined: set[uuid.UUID] = set()
                for source in results:
                    if (
                        source.element_kind == "table"
                        and source.element_id
                        and source.element_id not in outlined
                    ):
                        from app.models.chunk import Chunk

                        chunk = await db.get(Chunk, source.chunk_id)
                        if chunk:
                            source.table_outline = await reader.table_outline(
                                db, chunk, label_limit=96
                            )
                        outlined.add(source.element_id)
                return service.response(item, results, result.degraded_reasons).model_dump(
                    mode="json"
                )

        read_task, heartbeat_task = (
            asyncio.create_task(retrieve()),
            asyncio.create_task(heartbeat()),
        )
        try:
            done, _ = await asyncio.wait(
                {read_task, heartbeat_task},
                timeout=max(0.01, (deadline - datetime.now(UTC)).total_seconds()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise AgentError("OPERATION_DEADLINE_EXCEEDED")
            if heartbeat_task in done:
                heartbeat_task.result()
            result_payload = read_task.result()
            async with sessions() as db, db.begin():
                await service.context(db, context_id, lock_documents=True)
                row = await db.scalar(
                    select(EvidenceOperation).where(EvidenceOperation.id == op_id).with_for_update()
                )
                if (
                    row
                    and row.status == "running"
                    and row.fencing_token == token
                    and row.lease_expiry
                    and service.utc(row.lease_expiry) > datetime.now(UTC)
                    and service.utc(row.deadline) > datetime.now(UTC)
                ):
                    row.status, row.result, row.finished_at, row.lease_expiry = (
                        "succeeded",
                        result_payload,
                        datetime.now(UTC),
                        None,
                    )
        finally:
            read_task.cancel()
            heartbeat_task.cancel()
            await asyncio.gather(read_task, heartbeat_task, return_exceptions=True)
    except asyncio.CancelledError:
        # Lease recovery owns uncertain work; never reset the persisted deadline.
        raise
    except (AgentError, AppError, EmbeddingError, TimeoutError) as exc:
        await fail_operation(op_id, token, getattr(exc, "code", "OPERATION_DEADLINE_EXCEEDED"))
    except Exception:
        await fail_operation(op_id, token, "INTERNAL_ERROR")
        logger.exception("evidence_worker_failed", operation_id=operation_id)
        raise


async def fail_operation(op_id: uuid.UUID, token: int | None, code: str) -> None:
    async with get_sessionmaker()() as db, db.begin():
        row = await db.scalar(
            select(EvidenceOperation).where(EvidenceOperation.id == op_id).with_for_update()
        )
        if (
            row
            and row.status not in service.TERMINAL
            and (token is None or row.fencing_token == token)
        ):
            row.status, row.error_code, row.finished_at, row.lease_expiry = (
                "failed",
                code,
                datetime.now(UTC),
                None,
            )

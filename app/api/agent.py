from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import LocalTokenCounter
from app.agent.contracts import AgentError, TokenCounter
from app.agent.skills import SkillRegistry
from app.api.errors import AppError, ConflictError, DependencyUnavailableError
from app.core.config import get_settings
from app.db.session import get_session, get_sessionmaker
from app.models.agent import AgentEvent, AgentRun
from app.schemas.agent import AgentRunCreate, AgentRunOut, SkillOut
from app.schemas.trace import AgentTraceOut
from app.services.agent_runs import (
    TERMINAL,
    cancel_run,
    create_run,
    get_run,
    resume_run,
    run_out,
    utc,
)
from app.services.agent_trace import get_trace

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


@router.get("/runs/{run_id}/trace", response_model=AgentTraceOut)
async def trace(
    run_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> AgentTraceOut:
    return await get_trace(db, run_id, get_settings())


def registry() -> SkillRegistry:
    settings = get_settings()
    if not settings.agent_runtime_enabled:
        raise DependencyUnavailableError(code="AGENT_DISABLED")
    try:
        return SkillRegistry(settings.agent_skill_root, settings.storage_dir / "skills" / "enabled")
    except AgentError as exc:
        raise DependencyUnavailableError(code=exc.code) from exc


async def token_counter() -> TokenCounter:
    settings = get_settings()
    try:
        counter = await asyncio.to_thread(LocalTokenCounter, settings.agent_tokenizer_path)
        saved = json.loads(
            (settings.agent_tokenizer_path / "agent_manifest.json").read_text(encoding="utf-8")
        )
        if (
            saved.get("model") != settings.llm_model
            or saved.get("tokenizer_signature") != counter.signature
        ):
            raise AgentError("TOKENIZER_CHANGED")
        if settings.agent_action_protocol == "native":
            from app.llm.base import LLMMessage

            counter.count_request([LLMMessage("user", "test")], [])
        return counter
    except (AgentError, OSError, ValueError) as exc:
        raise DependencyUnavailableError(
            code=exc.code if isinstance(exc, AgentError) else "TOKENIZER_UNAVAILABLE"
        ) from exc


async def dispatch() -> None:
    from app.workers.agent import dispatch_pending

    await dispatch_pending({})


@router.get("/skills", response_model=list[SkillOut])
async def skills(catalog: Annotated[SkillRegistry, Depends(registry)]) -> list[SkillOut]:
    return [
        SkillOut(name=s.name, description=s.description, digest=s.digest)
        for s in catalog.skills.values()
    ]


@router.post("/runs", response_model=AgentRunOut, status_code=202)
async def start_run(
    body: AgentRunCreate,
    tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_session)],
    catalog: Annotated[SkillRegistry, Depends(registry)],
    counter: Annotated[TokenCounter, Depends(token_counter)],
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
) -> AgentRunOut:
    if counter.count(body.query) > 800 or not body.query.strip():
        error = AppError(
            code="QUERY_TOO_LONG" if body.query.strip() else "QUERY_EMPTY",
            message="Question exceeds the Agent input contract.",
        )
        error.status = 422
        raise error
    try:
        run = await create_run(db, body, idempotency_key, get_settings(), catalog)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        existing = await db.scalar(
            select(AgentRun).where(AgentRun.idempotency_key == idempotency_key)
        )
        if existing is None:
            raise ConflictError(code="SESSION_RUN_ACTIVE") from exc
        run = await create_run(db, body, idempotency_key, get_settings(), catalog)
    except AgentError as exc:
        error = AppError(code=exc.code, message=exc.code)
        error.status = 404 if exc.code == "SKILL_NOT_FOUND" else 422
        raise error from exc
    result = await run_out(db, run)
    tasks.add_task(dispatch)
    return result


@router.get("/runs/{run_id}", response_model=AgentRunOut)
async def read_run(
    run_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_session)]
) -> AgentRunOut:
    return await run_out(db, await get_run(db, run_id))


@router.post("/runs/{run_id}/cancel", response_model=AgentRunOut, status_code=202)
async def cancel(
    run_id: uuid.UUID, db: Annotated[AsyncSession, Depends(get_session)]
) -> AgentRunOut:
    return await run_out(db, await cancel_run(db, run_id))


@router.post("/runs/{run_id}/resume", response_model=AgentRunOut, status_code=202)
async def resume(
    run_id: uuid.UUID,
    tasks: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_session)],
    catalog: Annotated[SkillRegistry, Depends(registry)],
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
) -> AgentRunOut:
    run = await resume_run(db, run_id, idempotency_key, get_settings(), catalog)
    await db.commit()
    tasks.add_task(dispatch)
    return await run_out(db, run)


@router.get("/runs/{run_id}/events")
async def events(
    run_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
    last_event_id: Annotated[int, Header(ge=0)] = 0,
) -> StreamingResponse:
    run = await get_run(db, run_id)
    if last_event_id > run.event_sequence:
        raise ConflictError(code="EVENT_CURSOR_INVALID")
    if run.finished_at and utc(run.finished_at) < datetime.now(UTC) - timedelta(
        days=get_settings().agent_retention_days
    ):
        error = AppError(code="EVENTS_EXPIRED", message="Event history has expired.")
        error.status = 410
        raise error
    # Release the request transaction; each poll uses a fresh short transaction.
    await db.commit()

    async def stream() -> AsyncIterator[str]:
        cursor = last_event_id
        idle = 0
        while not await request.is_disconnected():
            async with get_sessionmaker()() as session:
                rows = list(
                    (
                        await session.execute(
                            select(AgentEvent)
                            .where(AgentEvent.run_id == run_id, AgentEvent.sequence > cursor)
                            .order_by(AgentEvent.sequence)
                            .limit(100)
                        )
                    ).scalars()
                )
                current = await session.get(AgentRun, run_id)
                for row in rows:
                    cursor = row.sequence
                    payload = {
                        "schema_version": 1,
                        "run_id": str(run_id),
                        "sequence": row.sequence,
                        "type": row.type,
                        "timestamp": utc(row.created_at).isoformat(),
                        "payload": row.payload,
                    }
                    yield f"id: {cursor}\nevent: {row.type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if current is None or (
                    current.status in TERMINAL and cursor >= current.event_sequence
                ):
                    return
            idle += 1
            if idle >= 20:
                yield ": heartbeat\n\n"
                idle = 0
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

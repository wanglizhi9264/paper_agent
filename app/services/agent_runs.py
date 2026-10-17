from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.context import compact_json
from app.agent.contracts import AgentError, RuntimeState
from app.agent.skills import SkillRegistry
from app.api.errors import ConflictError, DependencyUnavailableError, NotFoundError
from app.core.config import Settings
from app.models.agent import (
    AgentCheckpoint,
    AgentEvent,
    AgentEvidence,
    AgentRun,
    AgentRunOutbox,
    AgentStep,
    SessionSummary,
)
from app.models.agent_run import AgentTrialRun
from app.models.enums import MessageRole, MessageStatus
from app.models.session import Message, Session
from app.schemas.agent import AgentRunCreate, AgentRunOut
from app.schemas.search import SearchScope
from app.services.agent_trial import CorpusTools, corpus_pins

TERMINAL = {"succeeded", "failed", "cancelled"}


def utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def manifest(settings: Settings, registry: SkillRegistry) -> dict[str, Any]:
    path = settings.agent_tokenizer_path / "agent_manifest.json"
    if not path.is_file():
        raise DependencyUnavailableError(code="TOKENIZER_UNAVAILABLE")
    if settings.agent_action_protocol == "native" and not settings.agent_supports_tools:
        raise DependencyUnavailableError(code="AGENT_CAPABILITY_UNSUPPORTED")
    return {
        "graph": "paper-agent-v1",
        "skills": registry.signature,
        "tokenizer": hashlib.sha256(path.read_bytes()).hexdigest(),
        "model": settings.llm_model,
        "provider": settings.llm_base_url,
        "context_limit": settings.llm_context_tokens,
        "protocol": settings.agent_action_protocol,
        "reasoning_effort": settings.agent_reasoning_effort,
        "embedding": [settings.embedding_model, settings.embedding_revision],
        "reranker": [settings.rerank_model, settings.rerank_revision],
    }


async def event(
    db: AsyncSession, run: AgentRun, kind: str, payload: dict[str, Any] | None = None
) -> None:
    run.event_sequence += 1
    db.add(
        AgentEvent(
            run_id=run.id,
            sequence=run.event_sequence,
            type=kind,
            payload=payload or {},
            created_at=datetime.now(UTC),
        )
    )


async def next_sequence(db: AsyncSession, session_id: uuid.UUID) -> int:
    await db.get(Session, session_id, with_for_update=True)
    current = await db.scalar(
        select(func.max(Message.message_sequence)).where(Message.session_id == session_id)
    )
    return int(current or 0) + 1


async def create_run(
    db: AsyncSession, body: AgentRunCreate, key: str, settings: Settings, registry: SkillRegistry
) -> AgentRun:
    if not settings.agent_runtime_enabled:
        raise DependencyUnavailableError(
            code="AGENT_DISABLED", message="Enable Paper Agent in configuration."
        )
    request_hash = hashlib.sha256(compact_json(body.model_dump(mode="json")).encode()).hexdigest()
    existing = await db.scalar(select(AgentRun).where(AgentRun.idempotency_key == key))
    if existing:
        if existing.request_hash != request_hash:
            raise ConflictError(code="IDEMPOTENCY_CONFLICT")
        return existing
    session = await db.get(Session, body.session_id, with_for_update=True)
    if session is None:
        raise NotFoundError(code="SESSION_NOT_FOUND")
    active = await db.scalar(
        select(AgentRun.id).where(
            AgentRun.session_id == session.id, AgentRun.status.not_in(TERMINAL)
        )
    )
    if active:
        raise ConflictError(code="SESSION_RUN_ACTIVE")
    trial = await db.scalar(
        select(AgentTrialRun.id).where(
            AgentTrialRun.session_id == session.id,
            AgentTrialRun.status.in_(["running", "interrupted"]),
        )
    )
    if trial:
        raise ConflictError(code="SESSION_RUN_ACTIVE")
    scope = SearchScope.model_validate({"type": session.scope_type, **session.scope_payload})
    pins = await corpus_pins(db, scope)
    pins["runtime"] = manifest(settings, registry)
    pins["trace_enabled"] = body.trace_enabled
    skills = {name: registry.get(name).digest for name in body.skill_names}
    run_id = uuid.uuid4()
    message = Message(
        session_id=session.id,
        role=MessageRole.USER,
        content=body.query,
        message_sequence=await next_sequence(db, session.id),
    )
    db.add(message)
    await db.flush()
    run = AgentRun(
        id=run_id,
        session_id=session.id,
        user_message_id=message.id,
        query=body.query,
        snapshot_id=uuid.UUID(pins["snapshot_id"]),
        idempotency_key=key,
        request_hash=request_hash,
        pins=pins,
        state=RuntimeState(loaded_skills=skills).model_dump(mode="json"),
        status="queued",
    )
    db.add(run)
    await db.flush()
    db.add(AgentRunOutbox(run_id=run.id, generation=0))
    await event(db, run, "run.created")
    return run


async def get_run(db: AsyncSession, run_id: uuid.UUID, *, lock: bool = False) -> AgentRun:
    run = await db.get(AgentRun, run_id, with_for_update=lock)
    if run is None:
        raise NotFoundError(code="RUN_NOT_FOUND")
    return run


async def run_out(db: AsyncSession, run: AgentRun) -> AgentRunOut:
    state = RuntimeState.model_validate(run.state)
    steps = list(
        (
            await db.execute(
                select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.sequence)
            )
        ).scalars()
    )
    return AgentRunOut(
        run_id=run.id,
        session_id=run.session_id,
        snapshot_id=uuid.UUID(run.pins["snapshot_id"]),
        model_id=str(run.pins["runtime"]["model"]),
        status=cast(Any, run.status),
        events_url=f"/api/v1/agent/runs/{run.id}/events",
        usage={
            "llm_calls": state.llm_calls,
            "tool_calls": state.tool_calls,
            "search_calls": state.search_calls,
            "tokens": state.tokens,
        },
        selected_skills=state.loaded_skills,
        stop_reason=state.stop_reason,
        error_code=run.error_code,
        final_message_id=run.final_message_id,
        answer=state.answer if run.status == "succeeded" else None,
        sources=state.packed_sources if run.status == "succeeded" else [],
        claims=state.claims if run.status == "succeeded" else [],
        steps=[
            {"sequence": s.sequence, "tool": s.tool_name, "status": s.status, "call_id": s.call_id}
            for s in steps
        ],
        created_at=run.created_at,
    )


async def terminate(db: AsyncSession, run: AgentRun, status: str, code: str | None) -> None:
    if run.status in TERMINAL:
        return
    run.status, run.error_code = status, code
    run.finished_at = datetime.now(UTC)
    run.lease_expiry = None
    run.snapshot_id = None
    await event(
        db, run, "run.completed" if status == "succeeded" else f"run.{status}", {"error_code": code}
    )


async def cancel_run(db: AsyncSession, run_id: uuid.UUID) -> AgentRun:
    run = await get_run(db, run_id, lock=True)
    if run.status not in TERMINAL:
        run.cancel_requested = True
        if run.status in {"queued", "interrupted"}:
            await terminate(db, run, "cancelled", None)
    return run


async def resume_run(
    db: AsyncSession, run_id: uuid.UUID, key: str, settings: Settings, registry: SkillRegistry
) -> AgentRun:
    run = await get_run(db, run_id, lock=True)
    # Replayed resume has no second dispatch generation.
    resume_key = run.pins.get("resume_key")
    if resume_key == key:
        return run
    if run.status != "interrupted":
        raise ConflictError(code="RUN_NOT_RESUMABLE")
    if run.pins["runtime"] != manifest(settings, registry):
        raise ConflictError(code="RESUME_INCOMPATIBLE")
    if run.deadline and utc(run.deadline) <= datetime.now(UTC):
        raise ConflictError(code="RUN_DEADLINE_EXCEEDED")
    run.pins = {**run.pins, "resume_key": key}
    run.status = "queued"
    run.error_code = None
    run.fencing_token += 1
    db.add(AgentRunOutbox(run_id=run.id, generation=run.fencing_token))
    await event(db, run, "run.resumed")
    return run


async def claim(db: AsyncSession, run_id: uuid.UUID, owner: str) -> tuple[AgentRun, int] | None:
    run = await get_run(db, run_id, lock=True)
    if run.status != "queued":
        return None
    now = datetime.now(UTC)
    if run.cancel_requested:
        await terminate(db, run, "cancelled", None)
        return None
    if run.deadline and utc(run.deadline) <= now:
        await terminate(db, run, "failed", "RUN_DEADLINE_EXCEEDED")
        return None
    run.status, run.lease_owner = "running", owner
    run.fencing_token += 1
    run.lease_expiry = now + timedelta(seconds=60)
    if run.started_at is None:
        run.started_at, run.deadline = now, now + timedelta(seconds=180)
    await event(db, run, "run.started", {"recovery_count": run.recovery_count})
    return run, run.fencing_token


class RunStore:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        run_id: uuid.UUID,
        token: int,
        tools: CorpusTools,
    ) -> None:
        self.sessions, self.run_id, self.token, self.tools = sessions, run_id, token, tools

    async def guarded(self, db: AsyncSession) -> AgentRun:
        run = await get_run(db, self.run_id, lock=True)
        if (
            run.status != "running"
            or run.fencing_token != self.token
            or not run.lease_expiry
            or utc(run.lease_expiry) < datetime.now(UTC)
        ):
            raise AgentError("LEASE_LOST")
        if run.cancel_requested:
            raise AgentError("RUN_CANCELLED")
        return run

    async def check(self) -> None:
        async with self.sessions() as db:
            await self.guarded(db)
            await self.tools.check(db)

    async def heartbeat(self) -> None:
        async with self.sessions() as db, db.begin():
            run = await self.guarded(db)
            run.lease_expiry = datetime.now(UTC) + timedelta(seconds=60)

    async def save(self, state: RuntimeState, kind: str) -> None:
        async with self.sessions() as db, db.begin():
            run = await self.guarded(db)
            await self.tools.check(db, lock=state.phase == "done")
            previous = RuntimeState.model_validate(run.state)
            if kind == "context.compacted" and state.summary:
                messages = list(
                    (
                        await db.execute(
                            select(Message)
                            .where(
                                Message.session_id == run.session_id,
                                Message.id != run.user_message_id,
                            )
                            .order_by(Message.created_at, Message.id)
                        )
                    ).scalars()
                )[:-2]
                state.summary["source_message_ids"] = [str(m.id) for m in messages]
                summary = await db.get(SessionSummary, run.session_id, with_for_update=True)
                covered = max((m.message_sequence or 0 for m in messages), default=0)
                if summary is None:
                    db.add(
                        SessionSummary(
                            session_id=run.session_id,
                            revision=1,
                            covered_through_sequence=covered,
                            summary_json=state.summary,
                            model_signature=hashlib.sha256(
                                compact_json(run.pins["runtime"]).encode()
                            ).hexdigest(),
                        )
                    )
                elif covered >= summary.covered_through_sequence:
                    summary.revision += 1
                    summary.covered_through_sequence = covered
                    summary.summary_json = state.summary
            run.state = state.model_dump(mode="json")
            run.revision += 1
            run.lease_expiry = datetime.now(UTC) + timedelta(seconds=60)
            payload: dict[str, Any] = {"llm_calls": state.llm_calls, "tool_calls": state.tool_calls}
            if state.action:
                payload.update(tool=state.action.tool, call_id=state.action.call_id)
                if run.pins.get("trace_enabled") and kind in {"tool.started", "action.selected"}:
                    payload["arguments"] = state.action.arguments
            if kind == "tool.started" and state.action:
                step = await db.scalar(
                    select(AgentStep).where(
                        AgentStep.run_id == run.id, AgentStep.call_id == state.action.call_id
                    )
                )
                if step is None:
                    count = await db.scalar(
                        select(func.count())
                        .select_from(AgentStep)
                        .where(AgentStep.run_id == run.id)
                    )
                    db.add(
                        AgentStep(
                            run_id=run.id,
                            sequence=int(count or 0) + 1,
                            call_id=state.action.call_id,
                            tool_name=state.action.tool,
                            args_hash=hashlib.sha256(
                                compact_json(state.action.arguments).encode()
                            ).hexdigest(),
                            status="running",
                        )
                    )
            if kind == "tool.completed" and state.observations:
                observation = state.observations[-1]
                step = await db.scalar(
                    select(AgentStep).where(
                        AgentStep.run_id == run.id, AgentStep.call_id == state.completed_call_id
                    )
                )
                if step:
                    step.status = "succeeded" if observation.status == "ok" else "failed"
                    step.result_json = observation.model_dump(mode="json")
                payload.update(
                    tool=observation.tool,
                    call_id=state.completed_call_id,
                    error_code=observation.error_code,
                )
                if observation.tool == "load_skill" and observation.status == "ok":
                    await event(db, run, "skill.loaded", {"skills": state.loaded_skills})
            for evidence in state.evidence:
                row = await db.scalar(
                    select(AgentEvidence).where(
                        AgentEvidence.run_id == run.id,
                        AgentEvidence.chunk_id == evidence.source.chunk_id,
                    )
                )
                if row:
                    row.provenance = evidence.model_dump(mode="json")
                else:
                    db.add(
                        AgentEvidence(
                            run_id=run.id,
                            chunk_id=evidence.source.chunk_id,
                            snapshot_id=uuid.UUID(run.pins["snapshot_id"]),
                            provenance=evidence.model_dump(mode="json"),
                        )
                    )
            if kind == "answer.started":
                if previous.attempt_id:
                    await event(db, run, "answer.reset", {"attempt_id": state.attempt_id})
                payload["attempt_id"] = state.attempt_id
            elif kind == "answer.delta":
                payload.update(
                    attempt_id=state.attempt_id,
                    text=state.partial_answer[len(previous.partial_answer) :],
                )
            elif kind == "answer.completed":
                payload.update(
                    answer=state.answer,
                    sources=[s.model_dump(mode="json") for s in state.packed_sources],
                    citations=state.citations,
                )
            await event(db, run, kind, payload)
            if state.phase == "done":
                message = Message(
                    session_id=run.session_id,
                    role=MessageRole.ASSISTANT,
                    status=MessageStatus.COMPLETE,
                    run_id=run.id,
                    content=state.answer or "",
                    citations=state.citations,
                    message_sequence=await next_sequence(db, run.session_id),
                )
                db.add(message)
                await db.flush()
                run.final_message_id = message.id
                await terminate(db, run, "succeeded", None)
            db.add(
                AgentCheckpoint(
                    run_id=run.id,
                    revision=run.revision,
                    schema_version=1,
                    graph_version="paper-agent-v1",
                    last_event_sequence=run.event_sequence,
                    state_json=run.state,
                )
            )

    async def fail(self, code: str) -> None:
        async with self.sessions() as db, db.begin():
            run = await get_run(db, self.run_id, lock=True)
            if run.fencing_token != self.token or run.status in TERMINAL:
                return
            await terminate(db, run, "cancelled" if code == "RUN_CANCELLED" else "failed", code)

    async def interrupt(self) -> None:
        async with self.sessions() as db, db.begin():
            run = await get_run(db, self.run_id, lock=True)
            if run.status == "running" and run.fencing_token == self.token:
                run.status = "interrupted"
                run.error_code = "INTERRUPTED"
                run.lease_expiry = None
                run.fencing_token += 1
                await event(db, run, "run.interrupted", {"reason": "worker_shutdown"})


async def reconcile(db: AsyncSession) -> None:
    now = datetime.now(UTC)
    interrupted = list(
        (
            await db.execute(
                select(AgentRun)
                .where(AgentRun.status == "interrupted", AgentRun.deadline < now)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    for interrupted_run in interrupted:
        await terminate(db, interrupted_run, "failed", "RUN_DEADLINE_EXCEEDED")
    runs = list(
        (
            await db.execute(
                select(AgentRun)
                .where(AgentRun.status == "running", AgentRun.lease_expiry < now)
                .with_for_update(skip_locked=True)
            )
        ).scalars()
    )
    for run in runs:
        if run.cancel_requested:
            await terminate(db, run, "cancelled", None)
        elif run.recovery_count >= 2 or (run.deadline and utc(run.deadline) <= now):
            await terminate(db, run, "failed", "RECOVERY_EXHAUSTED")
        else:
            run.recovery_count += 1
            run.fencing_token += 1
            run.status = "queued"
            await event(db, run, "run.interrupted", {"reason": "lease_expired"})
            db.add(AgentRunOutbox(run_id=run.id, generation=run.fencing_token))


async def prune(db: AsyncSession, days: int) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    ids = select(AgentRun.id).where(AgentRun.status.in_(TERMINAL), AgentRun.finished_at < cutoff)
    for model in (AgentStep, AgentEvidence, AgentEvent, AgentCheckpoint):
        await db.execute(delete(model).where(model.run_id.in_(ids)))
    # Detailed retrieval diagnostics are also copied into the recovery checkpoint.
    # Clearing only AgentStep/Event would otherwise retain them indefinitely.
    runs = list((await db.execute(select(AgentRun).where(AgentRun.id.in_(ids)))).scalars())
    for run in runs:
        state = RuntimeState.model_validate(run.state)
        if state.context_trace or any(o.trace is not None for o in state.observations):
            state.context_trace = {}
            for observation in state.observations:
                observation.trace = None
            run.state = state.model_dump(mode="json")

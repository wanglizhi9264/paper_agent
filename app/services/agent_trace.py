from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.contracts import RuntimeState
from app.core.config import Settings
from app.models.agent import AgentEvent, AgentStep
from app.models.chunk import Chunk
from app.models.document import Document
from app.schemas.trace import AgentTraceOut, TraceCandidate, TraceEvent, TraceStep
from app.services.agent_runs import get_run, utc


async def get_trace(db: AsyncSession, run_id: uuid.UUID, settings: Settings) -> AgentTraceOut:
    """Read recorded execution; never run retrieval again to fill missing history."""
    run = await get_run(db, run_id)
    state = RuntimeState.model_validate(run.state)
    expired = bool(
        run.finished_at
        and utc(run.finished_at) < datetime.now(UTC) - timedelta(days=settings.agent_retention_days)
    )
    enabled = bool(run.pins.get("trace_enabled"))
    events = (
        list(
            (
                await db.execute(
                    select(AgentEvent)
                    .where(AgentEvent.run_id == run.id)
                    .order_by(AgentEvent.sequence)
                )
            ).scalars()
        )
        if not expired
        else []
    )
    steps = (
        list(
            (
                await db.execute(
                    select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.sequence)
                )
            ).scalars()
        )
        if not expired
        else []
    )
    step_outputs: list[TraceStep] = []
    for step in steps:
        starts = [
            e
            for e in events
            if e.type == "tool.started" and e.payload.get("call_id") == step.call_id
        ]
        ends = [
            e
            for e in events
            if e.type == "tool.completed" and e.payload.get("call_id") == step.call_id
        ]
        result = step.result_json or {}
        retrieval = result.get("trace") if enabled else None
        # Only identifiers returned by this search, pinned to this run's corpus.
        mapped = retrieval.get("candidate_chunk_ids", {}) if isinstance(retrieval, dict) else {}
        candidate_ids: list[uuid.UUID] = []
        for value in mapped.values():
            try:
                candidate_ids.append(uuid.UUID(str(value)))
            except ValueError:
                continue
        candidate_ids = list(dict.fromkeys(candidate_ids))
        candidates = []
        if candidate_ids:
            rows = (
                await db.execute(
                    select(Chunk, Document)
                    .join(Document, Document.id == Chunk.document_id)
                    .where(
                        Chunk.id.in_(candidate_ids[:200]),
                        Chunk.document_id.in_([uuid.UUID(k) for k in run.pins["documents"]]),
                        Document.status.not_in(["deleting", "deleted"]),
                    )
                )
            ).all()
            for chunk, document in rows:
                if run.pins["documents"].get(str(document.id)) != str(chunk.document_version_id):
                    continue
                candidates.append(
                    TraceCandidate(
                        chunk_id=chunk.id,
                        document_id=document.id,
                        document_title=document.title or document.filename,
                        section_path=chunk.section_path,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                        raw_content=chunk.raw_content[:1200],
                        retrieval_content=chunk.retrieval_content[:1200],
                        raw_truncated=len(chunk.raw_content) > 1200,
                        retrieval_truncated=len(chunk.retrieval_content) > 1200,
                    )
                )
        start = utc(starts[-1].created_at) if starts else None
        end = utc(ends[-1].created_at) if ends else None
        step_outputs.append(
            TraceStep(
                sequence=step.sequence,
                call_id=step.call_id,
                tool=step.tool_name,
                status=step.status,
                arguments=starts[-1].payload.get("arguments") if starts and enabled else None,
                started_at=start,
                completed_at=end,
                duration_ms=max(0, (end - start).total_seconds() * 1000) if start and end else None,
                error_code=result.get("error_code"),
                output=result.get("data"),
                retrieval=retrieval,
                candidates=candidates,
                candidates_truncated=len(candidate_ids) > 200,
                unavailable_candidates=len(candidate_ids[:200]) - len(candidates),
            )
        )
    return AgentTraceOut(
        run_id=run.id,
        query=run.query,
        status=run.status,
        availability="expired" if expired else "recorded" if enabled else "not_recorded",
        created_at=run.created_at,
        finished_at=run.finished_at,
        snapshot_id=uuid.UUID(run.pins["snapshot_id"]),
        scope=run.pins["scope"],
        model_id=str(run.pins["runtime"]["model"]),
        context_limit=run.pins["runtime"].get("context_limit"),
        loaded_skills=state.loaded_skills,
        usage={
            "llm_calls": state.llm_calls,
            "tool_calls": state.tool_calls,
            "tokens": state.tokens,
        },
        stop_reason=state.stop_reason,
        error_code=run.error_code,
        answer=state.answer if run.status == "succeeded" else None,
        steps=step_outputs,
        events=[
            TraceEvent(
                sequence=e.sequence,
                type=e.type,
                timestamp=e.created_at,
                # Answer deltas belong to the answer stream, not the diagnostic timeline.
                payload=cast(
                    dict[str, JsonValue], {k: v for k, v in e.payload.items() if k != "text"}
                ),
            )
            for e in events
        ],
        sources=state.packed_sources if run.status == "succeeded" else [],
        observed_evidence_count=len(state.evidence),
        context=state.context_trace if not expired else {},
    )

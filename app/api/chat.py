from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.agent.context import LocalTokenCounter, build_grounded_context
from app.agent.contracts import AgentError
from app.api.errors import ConflictError, DependencyUnavailableError, NotFoundError
from app.core.config import get_settings
from app.db.session import get_session
from app.embedding.registry import get_embedding_provider
from app.llm.base import LLMError, LLMMessage, ReasoningEffort
from app.llm.citations import RefusalStreamGate, finalize_answer
from app.llm.openai_compatible import get_llm_provider
from app.llm.prompts import build_messages, build_system_prompt
from app.models.agent import AgentRun
from app.models.enums import MessageRole, MessageStatus
from app.models.session import Message, Session
from app.schemas.chat import BBoxOut, ChatRequest, ChatResponse, CitationOut, SourceOut
from app.schemas.search import SearchResponse, SearchScope
from app.services.evidence_workflow import gather_chat_evidence
from app.services.query_rewrite import rewrite_query

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


def _generation_effort() -> ReasoningEffort | None:
    settings = get_settings()
    if not settings.agent_runtime_enabled or settings.agent_reasoning_effort == "provider_default":
        return None
    return settings.agent_reasoning_effort


def _scope(item: Session) -> SearchScope:
    value = item.scope_type if type(item.scope_type) is str else item.scope_type.value
    return SearchScope(
        type=cast(Literal["all", "documents", "collection"], value), **item.scope_payload
    )


async def _prepare(
    db: AsyncSession, body: ChatRequest
) -> tuple[
    Session,
    Message,
    SearchResponse,
    list[LLMMessage],
    dict[int, str],
    list[SourceOut],
]:
    item = await db.get(Session, body.session_id)
    if item is None:
        raise NotFoundError(code="SESSION_NOT_FOUND", message="Session was not found.")
    active = await db.scalar(
        select(AgentRun.id).where(
            AgentRun.session_id == item.id,
            AgentRun.status.in_(["queued", "running", "interrupted"]),
        )
    )
    if active:
        raise ConflictError(code="SESSION_RUN_ACTIVE")
    settings = get_settings()
    recent = list(
        (
            await db.execute(
                select(Message)
                .where(Message.session_id == item.id, Message.status == MessageStatus.COMPLETE)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(40 if settings.agent_runtime_enabled else 4)
            )
        ).scalars()
    )
    recent.reverse()
    history: list[tuple[str, str]] = [
        (
            message.role if isinstance(message.role, str) else message.role.value,
            message.content,
        )
        for message in recent
    ]
    llm_provider = get_llm_provider(settings)
    scope = _scope(item)
    rewrite = await rewrite_query(
        llm_provider,
        history,
        body.query,
        scope,
        language_strategy=settings.chat_rewrite_language,
        reasoning_effort=(
            None
            if settings.chat_planner_reasoning_effort == "provider_default"
            else settings.chat_planner_reasoning_effort
        ),
    )
    user = Message(session_id=item.id, role=MessageRole.USER, content=body.query)
    db.add(user)
    search = await gather_chat_evidence(
        db,
        rewrite.rewrite,
        scope,
        get_embedding_provider(),
        llm_provider,
        settings,
        original_query=body.query,
    )
    search.degraded_reasons.extend(
        reason for reason in rewrite.degraded_reasons if reason not in search.degraded_reasons
    )
    sources: list[SourceOut] = []
    blocks: list[str] = []
    citation_map: dict[int, str] = {}
    for index, result in enumerate(search.results, start=1):
        citation_map[index] = str(result.chunk_id)
        source = SourceOut(
            index=index,
            chunk_id=result.chunk_id,
            document_id=result.document_id,
            document_title=result.document_title,
            section_path=result.section_path,
            page=str(result.page_start or "unknown"),
            page_start=result.page_start,
            page_end=result.page_end,
            element_id=result.element_id,
            element_kind=result.element_kind,
            cell_ids=result.cell_ids,
            bboxes=[BBoxOut.model_validate(box) for box in result.bboxes],
            content=result.raw_content
            if settings.agent_runtime_enabled
            else result.context_content,
            truncated=False,
        )
        sources.append(source)
        blocks.append(
            f"[Source {index}]\nDocument: {result.document_title}\n"
            f"Section: {' > '.join(result.section_path)}\nPage: {result.page_start or 'unknown'}\n"
            f"Chunk-ID: {result.chunk_id}\nContent:\n{result.context_content}"
        )
    messages = build_messages(build_system_prompt("\n\n".join(blocks)), history, body.query)
    if settings.agent_runtime_enabled:
        try:
            counter = await asyncio.to_thread(LocalTokenCounter, settings.agent_tokenizer_path)
            messages, sources = build_grounded_context(
                sources,
                [LLMMessage(role, content) for role, content in history],
                body.query,
                counter,
                settings.llm_context_tokens,
            )
            citation_map = {s.index: str(s.chunk_id) for s in sources}
        except AgentError as exc:
            raise DependencyUnavailableError(code=exc.code) from exc
    return item, user, search, messages, citation_map, sources


@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest, db: Annotated[AsyncSession, Depends(get_session)]
) -> ChatResponse:
    _item, _user, search, messages, citation_map, sources = await _prepare(db, body)
    try:
        response = await get_llm_provider().generate(
            messages,
            max_tokens=1500 if get_settings().agent_runtime_enabled else None,
            reasoning_effort=_generation_effort(),
        )
    except LLMError as exc:
        raise DependencyUnavailableError(
            code="LLM_UNAVAILABLE", message="LLM is unavailable."
        ) from exc
    answer, citations, _invalid = finalize_answer(response.text, citation_map)
    citation_models = [
        CitationOut(index=c.index, chunk_id=uuid.UUID(c.chunk_id)) for c in citations
    ]
    citation_data = [citation.model_dump(mode="json") for citation in citation_models]
    assistant = Message(
        session_id=body.session_id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.COMPLETE,
        content=answer,
        citations=citation_data,
    )
    db.add(assistant)
    await db.flush()
    return ChatResponse(
        message_id=assistant.id,
        answer=answer,
        citations=citation_models,
        sources=sources,
        rewritten_query=search.rewritten_query,
        degraded_reasons=search.degraded_reasons,
        retrieval_queries=search.retrieval_queries,
        retrieval_routes=search.retrieval_routes,
        retrieval_workflow=search.retrieval_workflow,
    )


@router.post("/stream")
async def chat_stream(
    body: ChatRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> StreamingResponse:
    _item, _user, search, messages, citation_map, sources = await _prepare(db, body)
    message_id = uuid.uuid4()

    async def events() -> AsyncIterator[str]:
        yield _event(
            "meta",
            {
                "request_id": getattr(request.state, "request_id", ""),
                "message_id": str(message_id),
                "rewritten_query": search.rewritten_query,
                "retrieval_queries": search.retrieval_queries,
                "retrieval_routes": [
                    route.model_dump(mode="json") for route in search.retrieval_routes
                ],
                "retrieval_workflow": search.retrieval_workflow,
            },
        )
        yield _event("sources", {"sources": [source.model_dump(mode="json") for source in sources]})
        parts: list[str] = []
        refusal_gate = RefusalStreamGate()
        try:
            async for chunk in get_llm_provider().stream(
                messages,
                max_tokens=1500 if get_settings().agent_runtime_enabled else None,
                reasoning_effort=_generation_effort(),
            ):
                if chunk.text:
                    visible = refusal_gate.feed(chunk.text)
                    if visible:
                        parts.append(visible)
                        yield _event("delta", {"text": visible})
            tail = refusal_gate.finish()
            if tail:
                parts.append(tail)
                yield _event("delta", {"text": tail})
            answer, citations, _invalid = finalize_answer("".join(parts), citation_map)
            citation_data = [{"index": c.index, "chunk_id": c.chunk_id} for c in citations]
            db.add(
                Message(
                    id=message_id,
                    session_id=body.session_id,
                    role=MessageRole.ASSISTANT,
                    status=MessageStatus.COMPLETE,
                    content=answer,
                    citations=citation_data,
                )
            )
            await db.commit()
            yield _event(
                "done",
                {
                    "usage": {},
                    "citations": citation_data,
                    "finish_reason": "stop",
                    "degraded_reasons": search.degraded_reasons,
                },
            )
        except LLMError:
            await db.rollback()
            yield _event(
                "error",
                {
                    "error": {
                        "code": "LLM_UNAVAILABLE",
                        "message": "LLM is unavailable.",
                        "request_id": getattr(request.state, "request_id", ""),
                    }
                },
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _event(name: str, data: dict[str, object]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

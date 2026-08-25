from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.api.errors import DependencyUnavailableError, NotFoundError
from app.db.session import get_session
from app.embedding.registry import get_embedding_provider
from app.llm.base import LLMMessage
from app.llm.citations import validate_citations
from app.llm.openai_compatible import get_llm_provider
from app.models.enums import MessageRole, MessageStatus
from app.models.session import Message, Session
from app.schemas.chat import ChatRequest, ChatResponse, SourceOut
from app.schemas.search import SearchRequest, SearchResponse, SearchScope
from app.services.query_rewrite import rewrite_query
from app.services.retrieval import search_corpus

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


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
    recent = list(
        (
            await db.execute(
                select(Message)
                .where(Message.session_id == item.id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(4)
            )
        ).scalars()
    )
    recent.reverse()
    history = [
        (
            message.role if isinstance(message.role, str) else message.role.value,
            message.content,
        )
        for message in recent
    ]
    rewrite = await rewrite_query(get_llm_provider(), history, body.query, _scope(item))
    user = Message(session_id=item.id, role=MessageRole.USER, content=body.query)
    db.add(user)
    search = await search_corpus(
        db,
        SearchRequest(query=rewrite.rewrite.retrieval_query(), scope=_scope(item), top_k=8),
        get_embedding_provider(),
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
            bboxes=result.bboxes,
            content=result.context_content,
            truncated=False,
        )
        sources.append(source)
        blocks.append(
            f"[Source {index}]\nDocument: {result.document_title}\n"
            f"Section: {' > '.join(result.section_path)}\nPage: {result.page_start or 'unknown'}\n"
            f"Chunk-ID: {result.chunk_id}\nContent:\n{result.context_content}"
        )
    messages = [
        LLMMessage(
            role="system",
            content="Answer only from Sources. Cite factual claims with [N].\n\n"
            + "\n\n".join(blocks),
        ),
        LLMMessage(role="user", content=body.query),
    ]
    return item, user, search, messages, citation_map, sources


@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest, db: Annotated[AsyncSession, Depends(get_session)]
) -> ChatResponse:
    _item, _user, search, messages, citation_map, sources = await _prepare(db, body)
    try:
        response = await get_llm_provider().generate(messages)
    except Exception as exc:
        raise DependencyUnavailableError(
            code="LLM_UNAVAILABLE", message="LLM is unavailable."
        ) from exc
    answer, citations, _invalid = validate_citations(response.text, citation_map)
    citation_data = [{"index": c.index, "chunk_id": c.chunk_id} for c in citations]
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
        citations=citation_data,
        sources=sources,
        rewritten_query=search.rewritten_query,
        degraded_reasons=search.degraded_reasons,
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
            },
        )
        yield _event(
            "sources", {"sources": [source.model_dump(mode="json") for source in sources]}
        )
        parts: list[str] = []
        try:
            async for chunk in get_llm_provider().stream(messages):
                if chunk.text:
                    parts.append(chunk.text)
                    yield _event("delta", {"text": chunk.text})
            answer, citations, _invalid = validate_citations("".join(parts), citation_map)
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
        except Exception:
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

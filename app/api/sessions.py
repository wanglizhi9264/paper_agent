from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import NotFoundError
from app.db.session import get_session
from app.models.enums import SessionScopeType
from app.models.session import Message, Session
from app.schemas.common import Page
from app.schemas.session import MessageOut, SessionCreate, SessionOut

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


def _session_out(item: Session) -> SessionOut:
    return SessionOut(
        id=item.id,
        title=item.title,
        scope_type=item.scope_type if type(item.scope_type) is str else item.scope_type.value,
        scope_payload=item.scope_payload,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.post("", response_model=SessionOut, status_code=201)
async def create_session(
    body: SessionCreate, session: Annotated[AsyncSession, Depends(get_session)]
) -> SessionOut:
    payload = body.scope.model_dump(exclude={"type"}, mode="json", exclude_none=True)
    item = Session(
        title=body.title,
        scope_type=SessionScopeType(body.scope.type),
        scope_payload=payload,
    )
    session.add(item)
    await session.flush()
    return _session_out(item)


@router.get("", response_model=Page[SessionOut])
async def list_sessions(
    session: Annotated[AsyncSession, Depends(get_session)], limit: int = 50
) -> Page[SessionOut]:
    items = list(
        (
            await session.execute(
                select(Session)
                .order_by(Session.created_at.desc(), Session.id)
                .limit(min(limit, 100))
            )
        ).scalars()
    )
    return Page(items=[_session_out(item) for item in items])


@router.get("/{session_id}", response_model=SessionOut)
async def get_session_item(
    session_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]
) -> SessionOut:
    item = await session.get(Session, session_id)
    if item is None:
        raise NotFoundError(code="SESSION_NOT_FOUND", message="Session was not found.")
    return _session_out(item)


@router.get("/{session_id}/messages", response_model=Page[MessageOut])
async def list_messages(
    session_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]
) -> Page[MessageOut]:
    if await session.get(Session, session_id) is None:
        raise NotFoundError(code="SESSION_NOT_FOUND", message="Session was not found.")
    messages = list(
        (
            await session.execute(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at, Message.id)
            )
        ).scalars()
    )
    return Page(
        items=[
            MessageOut(
                id=item.id,
                role=item.role if type(item.role) is str else item.role.value,
                status=item.status if type(item.status) is str else item.status.value,
                content=item.content,
                citations=item.citations,
                created_at=item.created_at,
            )
            for item in messages
        ]
    )


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]
) -> Response:
    if await session.get(Session, session_id) is None:
        raise NotFoundError(code="SESSION_NOT_FOUND", message="Session was not found.")
    await session.execute(delete(Session).where(Session.id == session_id))
    return Response(status_code=204)

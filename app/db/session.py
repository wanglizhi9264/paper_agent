from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session as SyncSession

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


@event.listens_for(SyncSession, "before_flush")
def assign_message_sequences(
    session: SyncSession, flush_context: object, instances: object
) -> None:
    from app.models.session import Message, Session

    pending = [
        obj for obj in session.new if isinstance(obj, Message) and obj.message_sequence is None
    ]
    sequences: dict[object, int] = {}
    for message in pending:
        if message.session_id not in sequences:
            session.execute(
                select(Session.id).where(Session.id == message.session_id).with_for_update()
            )
            existing = session.scalar(
                select(func.max(Message.message_sequence)).where(
                    Message.session_id == message.session_id
                )
            )
            assigned = [
                obj.message_sequence or 0
                for obj in session.new
                if isinstance(obj, Message) and obj.session_id == message.session_id
            ]
            sequences[message.session_id] = max(int(existing or 0), *assigned)
        sequences[message.session_id] += 1
        message.message_sequence = sequences[message.session_id]


def _build_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
    )


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a transactional AsyncSession."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Standalone async context manager for worker/service code."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None

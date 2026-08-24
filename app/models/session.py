from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.types import jsonb
from app.models.enums import MessageRole, MessageStatus, SessionScopeType
from app.models.mixins import TimestampMixin


class Session(Base, TimestampMixin):
    """A chat session with a fixed retrieval scope (spec §9.6)."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="Untitled")
    scope_type: Mapped[SessionScopeType] = mapped_column(String(16), nullable=False)
    scope_payload: Mapped[dict[str, Any]] = mapped_column(jsonb(), nullable=False, default=dict)

    messages: Mapped[list[Message]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.created_at",
    )

    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('all','documents','collection')", name="ck_session_scope_type"
        ),
        CheckConstraint("length(title) BETWEEN 1 AND 200", name="ck_session_title_length"),
        Index("ix_sessions_created_at", "created_at"),
    )


class Message(Base):
    """A single message in a session (spec §9.6).

    User messages and the final assistant message are persisted. A stream
    interrupted mid-flight is saved with ``status=interrupted`` and its partial
    content, never a fabricated complete answer.
    """

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE", name="fk_message_session"),
        nullable=False,
    )
    role: Mapped[MessageRole] = mapped_column(String(16), nullable=False)
    status: Mapped[MessageStatus] = mapped_column(
        String(16), nullable=False, default=MessageStatus.COMPLETE
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(jsonb(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    session: Mapped[Session] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint("role IN ('user','assistant','system')", name="ck_message_role_enum"),
        CheckConstraint("status IN ('complete','interrupted')", name="ck_message_status_enum"),
        Index("ix_messages_session_id", "session_id"),
        Index("ix_messages_created_at", "created_at"),
    )

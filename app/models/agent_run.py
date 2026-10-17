from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import jsonb
from app.models.mixins import TimestampMixin


class AgentTrialRun(Base, TimestampMixin):
    """CLI trial journal: state and events commit atomically in one row."""

    __tablename__ = "agent_trial_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    query: Mapped[str] = mapped_column(Text, nullable=False)
    pins: Mapped[dict[str, Any]] = mapped_column(jsonb(), nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(jsonb(), nullable=False)
    events: Mapped[list[dict[str, Any]]] = mapped_column(jsonb(), nullable=False, default=list)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    final_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('running','interrupted','succeeded','failed')", name="ck_agent_trial_status"
        ),
        CheckConstraint("length(query) BETWEEN 1 AND 4000", name="ck_agent_trial_query"),
        Index(
            "uq_agent_trial_active_session",
            "session_id",
            unique=True,
            postgresql_where=text("status IN ('running','interrupted')"),
            sqlite_where=text("status IN ('running','interrupted')"),
        ),
    )

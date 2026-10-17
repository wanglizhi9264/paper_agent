from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import jsonb
from app.models.mixins import TimestampMixin


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    user_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE")
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    query: Mapped[str] = mapped_column(Text)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("index_snapshots.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(16), default="queued")
    pins: Mapped[dict[str, Any]] = mapped_column(jsonb())
    state: Mapped[dict[str, Any]] = mapped_column(jsonb())
    revision: Mapped[int] = mapped_column(default=0)
    event_sequence: Mapped[int] = mapped_column(default=0)
    fencing_token: Mapped[int] = mapped_column(default=0)
    recovery_count: Mapped[int] = mapped_column(default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    final_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','interrupted','succeeded','failed','cancelled')",
            name="ck_agent_status",
        ),
        CheckConstraint(
            "revision >= 0 AND event_sequence >= 0 AND fencing_token >= 0 AND recovery_count BETWEEN 0 AND 2",
            name="ck_agent_counters",
        ),
        CheckConstraint("length(query) BETWEEN 1 AND 4000", name="ck_agent_query"),
        CheckConstraint(
            "(status IN ('succeeded','failed','cancelled')) = (finished_at IS NOT NULL)",
            name="ck_agent_finished",
        ),
        Index(
            "uq_agent_session_active",
            "session_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running','interrupted')"),
            sqlite_where=text("status IN ('queued','running','interrupted')"),
        ),
    )


class AgentEvent(Base):
    __tablename__ = "agent_events"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column()
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(jsonb())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("run_id", "sequence"), CheckConstraint("sequence > 0"))


class AgentCheckpoint(Base):
    __tablename__ = "agent_checkpoints"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column()
    schema_version: Mapped[int] = mapped_column(default=1)
    graph_version: Mapped[str] = mapped_column(String(40), default="paper-agent-v1")
    last_event_sequence: Mapped[int] = mapped_column()
    state_json: Mapped[dict[str, Any]] = mapped_column(jsonb())
    __table_args__ = (UniqueConstraint("run_id", "revision"), CheckConstraint("revision >= 0"))


class AgentStep(Base):
    __tablename__ = "agent_steps"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column()
    call_id: Mapped[str] = mapped_column(String(128))
    tool_name: Mapped[str] = mapped_column(String(64))
    tool_version: Mapped[str] = mapped_column(String(20), default="1")
    args_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    result_json: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    __table_args__ = (
        UniqueConstraint("run_id", "sequence"),
        UniqueConstraint("run_id", "call_id"),
        CheckConstraint("status IN ('running','succeeded','failed')"),
    )


class AgentEvidence(Base):
    __tablename__ = "agent_evidence"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    chunk_id: Mapped[uuid.UUID] = mapped_column()
    snapshot_id: Mapped[uuid.UUID] = mapped_column()
    provenance: Mapped[dict[str, Any]] = mapped_column(jsonb())
    __table_args__ = (UniqueConstraint("run_id", "chunk_id"),)


class AgentRunOutbox(Base):
    __tablename__ = "agent_run_outbox"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    generation: Mapped[int] = mapped_column(default=0)
    dispatched: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (UniqueConstraint("run_id", "generation"),)


class SessionSummary(Base, TimestampMixin):
    __tablename__ = "session_summaries"
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(default=0)
    covered_through_sequence: Mapped[int] = mapped_column(default=0)
    summary_json: Mapped[dict[str, Any]] = mapped_column(jsonb())
    model_signature: Mapped[str] = mapped_column(String(64))

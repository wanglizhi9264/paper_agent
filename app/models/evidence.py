from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import jsonb


class EvidenceContext(Base):
    __tablename__ = "evidence_contexts"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("index_snapshots.id", ondelete="RESTRICT")
    )
    pins: Mapped[dict[str, Any]] = mapped_column(jsonb())
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "status IN ('active','expired','invalidated')", name="ck_evidence_context_status"
        ),
        CheckConstraint(
            "(status = 'active') = (snapshot_id IS NOT NULL)", name="ck_evidence_context_snapshot"
        ),
        CheckConstraint("expires_at > created_at", name="ck_evidence_context_time"),
        Index("ix_evidence_context_expiry", "status", "expires_at"),
    )


class EvidenceOperation(Base):
    __tablename__ = "evidence_operations"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    context_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_contexts.id", ondelete="CASCADE")
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    request: Mapped[dict[str, Any]] = mapped_column(jsonb())
    status: Mapped[str] = mapped_column(String(16), default="queued")
    result: Mapped[dict[str, Any] | None] = mapped_column(jsonb())
    error_code: Mapped[str | None] = mapped_column(String(64))
    fencing_token: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')",
            name="ck_evidence_op_status",
        ),
        CheckConstraint(
            "attempts BETWEEN 0 AND 2 AND fencing_token >= 0", name="ck_evidence_op_counters"
        ),
        CheckConstraint(
            "(status IN ('succeeded','failed','cancelled')) = (finished_at IS NOT NULL)",
            name="ck_evidence_op_finished",
        ),
        CheckConstraint("deadline > created_at", name="ck_evidence_op_time"),
        Index("ix_evidence_op_pending", "status", "deadline"),
        Index("ix_evidence_op_context", "context_id"),
    )

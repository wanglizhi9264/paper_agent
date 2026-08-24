from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import jsonb
from app.models.mixins import TimestampMixin


class RetrievalLog(Base, TimestampMixin):
    """Diagnostic record of one retrieval/generation request (spec §9.6).

    Candidates store only id/rank/score/preview to bound growth. Full prompts,
    raw content and API keys are never logged.
    """

    __tablename__ = "retrieval_logs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL", name="fk_retrlog_session"),
        nullable=True,
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL", name="fk_retrlog_message"),
        nullable=True,
    )
    original_query: Mapped[str] = mapped_column(Text, nullable=False)
    rewritten_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    rewrite_fallback: Mapped[bool] = mapped_column(default=False, nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(jsonb(), nullable=False)
    params_snapshot: Mapped[dict[str, Any]] = mapped_column(jsonb(), nullable=False, default=dict)
    model_versions: Mapped[dict[str, Any] | None] = mapped_column(jsonb(), nullable=True)
    bm25_candidates: Mapped[list[dict[str, Any]] | None] = mapped_column(jsonb(), nullable=True)
    dense_candidates: Mapped[list[dict[str, Any]] | None] = mapped_column(jsonb(), nullable=True)
    rrf_candidates: Mapped[list[dict[str, Any]] | None] = mapped_column(jsonb(), nullable=True)
    rerank_candidates: Mapped[list[dict[str, Any]] | None] = mapped_column(jsonb(), nullable=True)
    expanded_candidates: Mapped[list[dict[str, Any]] | None] = mapped_column(jsonb(), nullable=True)
    final_context: Mapped[dict[str, Any] | None] = mapped_column(jsonb(), nullable=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(jsonb(), nullable=True)
    degraded_reasons: Mapped[list[dict[str, Any]] | None] = mapped_column(jsonb(), nullable=True)
    timings_ms: Mapped[dict[str, Any] | None] = mapped_column(jsonb(), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_retrlog_session_id", "session_id"),
        Index("ix_retrlog_created_at", "created_at"),
        CheckConstraint(
            "length(original_query) BETWEEN 1 AND 4000", name="ck_retrlog_query_length"
        ),
    )

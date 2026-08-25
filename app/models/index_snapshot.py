from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.types import jsonb
from app.models.enums import IndexSnapshotStatus
from app.models.mixins import TimestampMixin


class IndexSnapshot(Base, TimestampMixin):
    """An immutable FAISS+BM25 snapshot of the whole retrievable corpus (spec §9.5).

    Only one snapshot is ``active`` at a time, referenced by ``system_state``.
    Built as a shadow, validated, then atomically activated.
    """

    __tablename__ = "index_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    status: Mapped[IndexSnapshotStatus] = mapped_column(
        String(24), nullable=False, default=IndexSnapshotStatus.BUILDING
    )
    embedding_signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    faiss_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    bm25_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    manifest: Mapped[dict[str, Any] | None] = mapped_column(jsonb(), nullable=True)
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_faiss_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('building','active','superseded','failed')",
            name="ck_snapshot_status_enum",
        ),
        CheckConstraint("document_count >= 0", name="ck_snapshot_doc_count"),
        CheckConstraint("chunk_count >= 0", name="ck_snapshot_chunk_count"),
        CheckConstraint(
            "(max_faiss_id IS NULL OR max_faiss_id >= 0)", name="ck_snapshot_max_faiss"
        ),
        Index("ix_snapshot_status", "status"),
        Index("ix_snapshot_signature", "embedding_signature"),
    )


class SystemState(Base):
    """Singleton row holding the active IndexSnapshot pointer (spec §9.5).

    Enforced as a single-row table via ``id`` CHECK constraint = 1.
    """

    __tablename__ = "system_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    active_index_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("index_snapshots.id", use_alter=True, name="fk_system_active_snapshot"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (CheckConstraint("id = 1", name="ck_system_state_singleton"),)

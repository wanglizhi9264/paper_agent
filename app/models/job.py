from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import JobKind, JobStage, JobStatus
from app.models.mixins import TimestampMixin


class IngestionJob(Base, TimestampMixin):
    """Asynchronous ingestion/reindex/delete job (spec §9.3).

    ``id`` is the external job id. Each retry creates a new row (a new attempt
    is a new job); the history of a logical operation is reconstructed via
    ``document_id`` + ``kind`` + ``created_at`` ordering.
    """

    __tablename__ = "ingestion_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE", name="fk_job_document"),
        nullable=False,
    )
    kind: Mapped[JobKind] = mapped_column(String(24), nullable=False)
    status: Mapped[JobStatus] = mapped_column(String(24), nullable=False, default=JobStatus.QUEUED)
    stage: Mapped[JobStage] = mapped_column(String(24), nullable=False, default=JobStage.QUEUED)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint("progress BETWEEN 0 AND 100", name="ck_job_progress_range"),
        CheckConstraint("attempt >= 1", name="ck_job_attempt_min"),
        CheckConstraint("kind IN ('ingest','reindex','delete_cleanup')", name="ck_job_kind_enum"),
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')",
            name="ck_job_status_enum",
        ),
        CheckConstraint(
            "stage IN ('queued','parsing','chunking','embedding','indexing','finalizing')",
            name="ck_job_stage_enum",
        ),
        Index("ix_jobs_document_id", "document_id"),
        Index("ix_jobs_status", "status"),
        Index("ix_jobs_created_at", "created_at"),
        # started_at must precede finished_at when both are present
        CheckConstraint(
            "(started_at IS NULL OR finished_at IS NULL OR started_at <= finished_at)",
            name="ck_job_time_order",
        ),
    )

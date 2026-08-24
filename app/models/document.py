from __future__ import annotations

import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import DocumentStatus
from app.models.mixins import TimestampMixin


class Document(Base, TimestampMixin):
    """A single uploaded paper (spec §9.1).

    ``active_document_version_id`` is a deferred, cycle-breaking FK to
    ``document_versions.id`` (use_alter). It is only switched after a successful
    reindex in the same transaction that activates the new IndexSnapshot.
    """

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    extension: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        String(24),
        nullable=False,
        default=DocumentStatus.UPLOADED,
    )
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(nullable=True)
    character_count: Mapped[int | None] = mapped_column(nullable=True)
    chunk_count: Mapped[int] = mapped_column(default=0, nullable=False)
    active_document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_versions.id", use_alter=True, name="fk_document_active_version"),
        nullable=True,
    )
    parser_version: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "file_size BETWEEN 1 AND 104857600",
            name="ck_document_file_size_range",
        ),
        CheckConstraint(
            "extension IN ('pdf','docx','md')",
            name="ck_document_extension_allowlist",
        ),
        CheckConstraint(
            "status IN ('uploaded','queued','parsing','chunking','embedding',"
            "'indexing','ready','failed','deleting','deleted')",
            name="ck_document_status_enum",
        ),
        Index("ix_documents_status", "status"),
        Index("ix_documents_sha256", "sha256"),
        Index("ix_documents_created_at", "created_at"),
    )

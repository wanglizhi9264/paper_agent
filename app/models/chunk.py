from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB  # noqa: F401 - re-exported for migrations
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.db.types import jsonb
from app.models.enums import DocumentVersionStatus
from app.models.mixins import TimestampMixin

# Non-negative int64 sequence for FAISS ids (spec §13.1). Allocated once per
# chunk that enters a dense index; stored on the chunk because chunks belong to
# an immutable DocumentVersion and the id is therefore stable across snapshots
# that include the same version.
FAISS_ID_SEQ = Sequence("faiss_id_seq", start=1, minvalue=1, data_type=Integer)


class DocumentVersion(Base, TimestampMixin):
    """An immutable parse/chunk result for one document (spec §9.5).

    A new row is created on every (re)ingest or embedding-model change. The
    Document's ``active_document_version_id`` points to the currently retrievable
    version; superseded versions are retained until safe reclamation.
    """

    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE", name="fk_version_document"),
        nullable=False,
    )
    status: Mapped[DocumentVersionStatus] = mapped_column(
        String(24), nullable=False, default=DocumentVersionStatus.BUILDING
    )
    parser_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    chunk_config: Mapped[dict[str, Any]] = mapped_column(jsonb(), nullable=False)
    embedding_model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    embedding_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    analyzer_config: Mapped[dict[str, Any] | None] = mapped_column(jsonb(), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    character_count: Mapped[int | None] = mapped_column(nullable=True)
    stats: Mapped[dict[str, Any] | None] = mapped_column(jsonb(), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document_version",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('building','ready','superseded','failed')",
            name="ck_docversion_status_enum",
        ),
        CheckConstraint("chunk_count >= 0", name="ck_docversion_chunk_count"),
        CheckConstraint(
            "(embedding_dimension IS NULL OR embedding_dimension > 0)",
            name="ck_docversion_dim_positive",
        ),
        Index("ix_docversion_document_id", "document_id"),
        Index("ix_docversion_status", "status"),
        Index("ix_docversion_signature", "embedding_signature"),
    )


class Chunk(Base):
    """An immutable retrieval unit belonging to one DocumentVersion (spec §9.4)."""

    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE", name="fk_chunk_document"),
        nullable=False,
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE", name="fk_chunk_version"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL", name="fk_chunk_parent"),
        nullable=True,
    )
    chapter_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL", name="fk_chunk_chapter"),
        nullable=True,
    )
    section_path: Mapped[list[str]] = mapped_column(jsonb(), nullable=False)
    page_start: Mapped[int | None] = mapped_column(nullable=True)
    page_end: Mapped[int | None] = mapped_column(nullable=True)
    line_start: Mapped[int | None] = mapped_column(nullable=True)
    line_end: Mapped[int | None] = mapped_column(nullable=True)
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    faiss_id: Mapped[int | None] = mapped_column(
        BigInteger,
        FAISS_ID_SEQ,
        nullable=True,
        unique=True,
    )
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", jsonb(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    document_version: Mapped[DocumentVersion] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("document_version_id", "chunk_index", name="uq_chunk_version_index"),
        CheckConstraint("chunk_index >= 0", name="ck_chunk_index_nonneg"),
        CheckConstraint("character_count >= 0", name="ck_chunk_char_count"),
        CheckConstraint(
            "kind IN ('text','title','table','code','chapter')", name="ck_chunk_kind_enum"
        ),
        CheckConstraint(
            "(page_start IS NULL OR page_end IS NULL OR page_start <= page_end)",
            name="ck_chunk_page_order",
        ),
        CheckConstraint(
            "(line_start IS NULL OR line_end IS NULL OR line_start <= line_end)",
            name="ck_chunk_line_order",
        ),
        CheckConstraint("(faiss_id IS NULL OR faiss_id >= 0)", name="ck_chunk_faiss_id_nonneg"),
        Index("ix_chunk_document_id", "document_id"),
        Index("ix_chunk_version_id", "document_version_id"),
        Index("ix_chunk_content_hash", "content_hash"),
        Index("ix_chunk_chapter", "chapter_chunk_id"),
    )

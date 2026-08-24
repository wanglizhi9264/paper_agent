from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.document import Document


class CollectionDocument(Base):
    """Many-to-many association (spec §9.2).

    Composite PK ``(collection_id, document_id)``. Deleting a Collection only
    removes associations; deleting a Document cascades to associations.
    """

    __tablename__ = "collection_documents"

    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE", name="fk_cd_collection"),
        primary_key=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE", name="fk_cd_document"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    collection: Mapped[Collection] = relationship(back_populates="documents")
    document: Mapped[Document] = relationship()

    __table_args__ = (Index("ix_cd_document_id", "document_id"),)


class Collection(Base, TimestampMixin):
    """User-managed grouping of documents (spec §9.2)."""

    __tablename__ = "collections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String(1000), nullable=False, default="")

    documents: Mapped[list[CollectionDocument]] = relationship(
        back_populates="collection",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint("length(name) BETWEEN 1 AND 120", name="ck_collection_name_length"),
        CheckConstraint("length(description) <= 1000", name="ck_collection_desc_length"),
    )

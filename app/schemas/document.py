from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, field_serializer

from app.schemas.common import CamelModel, to_rfc3339


class DocumentOut(CamelModel):
    id: uuid.UUID
    filename: str
    media_type: str
    extension: str
    title: str | None = None
    sha256: str
    file_size: int
    status: str
    status_message: str | None = None
    page_count: int | None = None
    character_count: int | None = None
    chunk_count: int = 0
    active_document_version_id: uuid.UUID | None = None
    parser_version: str | None = None
    collection_ids: list[uuid.UUID] = Field(default_factory=list)
    active_job_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def _ser_dt(self, value: datetime) -> str | None:
        return to_rfc3339(value)


class DocumentCreateResponse(CamelModel):
    document_id: uuid.UUID
    job_id: uuid.UUID
    status: str = "queued"


class ChunkOut(CamelModel):
    id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    chunk_index: int
    kind: str
    parent_chunk_id: uuid.UUID | None = None
    chapter_chunk_id: uuid.UUID | None = None
    section_path: list[str]
    page_start: int | None = None
    page_end: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    raw_content: str
    retrieval_content: str
    content_hash: str
    character_count: int
    token_count: int | None = None
    created_at: datetime

    @field_serializer("created_at")
    def _ser_created(self, value: datetime) -> str | None:
        return to_rfc3339(value)

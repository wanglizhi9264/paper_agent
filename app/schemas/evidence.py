from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from app.schemas.chat import SourceOut
from app.schemas.search import SearchScope


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PaperListRequest(StrictSchema):
    scope: SearchScope = Field(default_factory=lambda: SearchScope(type="all"))
    query: str = Field(default="", max_length=1000)
    cursor: uuid.UUID | None = None
    limit: int = Field(default=20, ge=1, le=50)


class PaperItem(StrictSchema):
    document_id: uuid.UUID
    title: str
    filename: str
    page_count: int | None


class PaperList(StrictSchema):
    schema_version: Literal[1] = 1
    items: list[PaperItem]
    next_cursor: uuid.UUID | None = None


class EvidenceSearch(StrictSchema):
    query: str = Field(min_length=1, max_length=4000)
    scope: SearchScope | None = None
    context_id: uuid.UUID | None = None
    top_k: int = Field(default=6, ge=1, le=8)

    @model_validator(mode="after")
    def exclusive_scope(self) -> Self:
        if self.scope is not None and self.context_id is not None:
            raise ValueError("Use scope for a new context or context_id, never both")
        return self


class ChunkRead(StrictSchema):
    chunk_id: uuid.UUID
    start_char: int = Field(default=0, ge=0)
    max_chars: int = Field(default=6000, ge=1, le=6000)


class EvidenceChunks(StrictSchema):
    context_id: uuid.UUID
    requests: list[ChunkRead] = Field(min_length=1, max_length=4)


class EvidenceTable(StrictSchema):
    context_id: uuid.UUID
    evidence_id: uuid.UUID
    rows: list[int] = Field(default_factory=list, max_length=40)
    cell_ids: list[uuid.UUID] = Field(default_factory=list, max_length=40)


class EvidenceResult(StrictSchema):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    document_title: str
    section_path: list[str]
    page_start: int | None
    page_end: int | None
    raw_content: str
    content_hash: str
    element_id: uuid.UUID | None = None
    element_kind: str | None = None
    cell_ids: list[uuid.UUID] = Field(default_factory=list)
    bboxes: list[dict[str, JsonValue]] = Field(default_factory=list)
    truncated: bool = False
    start_char: int = 0
    next_start_char: int | None = None
    table_outline: JsonValue = None

    @classmethod
    def from_source(
        cls, source: SourceOut, version_id: uuid.UUID, content_hash: str
    ) -> EvidenceResult:
        return cls(
            **source.model_dump(exclude={"index", "page", "content"}),
            raw_content=source.content,
            document_version_id=version_id,
            content_hash=content_hash,
        )


class EvidenceResponse(StrictSchema):
    schema_version: Literal[1] = 1
    context_id: uuid.UUID
    snapshot_id: uuid.UUID
    expires_at: datetime
    results: list[EvidenceResult]
    degraded_reasons: list[str] = Field(default_factory=list)


class EvidenceOperationOut(StrictSchema):
    operation_id: uuid.UUID
    context_id: uuid.UUID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    error_code: str | None
    result: EvidenceResponse | None

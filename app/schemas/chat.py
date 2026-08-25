from __future__ import annotations

import uuid
from typing import Self

from pydantic import Field, model_validator

from app.schemas.common import CamelModel


class ChatRequest(CamelModel):
    session_id: uuid.UUID
    query: str = Field(min_length=1, max_length=4000)


class BBoxOut(CamelModel):
    physical_page: int = Field(ge=1)
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def valid_box(self) -> Self:
        if self.x0 > self.x1 or self.y0 > self.y1:
            raise ValueError("bbox coordinates are reversed")
        return self


class SourceOut(CamelModel):
    index: int = Field(ge=1)
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    section_path: list[str]
    page: str
    page_start: int | None
    page_end: int | None
    element_id: uuid.UUID | None = None
    element_kind: str | None = None
    cell_ids: list[uuid.UUID] = Field(default_factory=list)
    bboxes: list[BBoxOut] = Field(default_factory=list)
    content: str
    truncated: bool

    @model_validator(mode="after")
    def table_provenance_is_complete(self) -> Self:
        if self.element_kind == "table" and (
            self.element_id is None
            or not self.cell_ids
            or not self.bboxes
            or self.page_start is None
            or self.page_end is None
        ):
            raise ValueError("V2 table sources require element, cell, page, and bbox provenance")
        return self


class CitationOut(CamelModel):
    index: int = Field(ge=1)
    chunk_id: uuid.UUID


class ChatResponse(CamelModel):
    message_id: uuid.UUID
    answer: str
    citations: list[CitationOut]
    sources: list[SourceOut]
    rewritten_query: str
    degraded_reasons: list[str]

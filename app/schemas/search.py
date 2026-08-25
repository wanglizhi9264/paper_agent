from __future__ import annotations

import uuid
from typing import Literal, Self

from pydantic import Field, model_validator

from app.schemas.common import CamelModel


class SearchScope(CamelModel):
    type: Literal["all", "documents", "collection"]
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    collection_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if self.type == "documents" and not self.document_ids:
            raise ValueError("documents scope requires document_ids")
        if self.type == "collection" and self.collection_id is None:
            raise ValueError("collection scope requires collection_id")
        if self.type != "documents" and self.document_ids:
            raise ValueError("document_ids are only valid for documents scope")
        if self.type != "collection" and self.collection_id is not None:
            raise ValueError("collection_id is only valid for collection scope")
        return self


class SearchRequest(CamelModel):
    query: str = Field(min_length=1, max_length=4000)
    scope: SearchScope = Field(default_factory=lambda: SearchScope(type="all"))
    top_k: int = Field(default=8, ge=1, le=20)
    minimum_should_match: int = Field(default=1, ge=1, le=20)
    debug: bool = False


class SearchResultOut(CamelModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    section_path: list[str]
    page_start: int | None
    page_end: int | None
    raw_content: str
    score: float
    rank: int


class SearchResponse(CamelModel):
    original_query: str
    rewritten_query: str
    results: list[SearchResultOut]
    degraded_reasons: list[str] = Field(default_factory=list)
    debug: dict[str, object] | None = None

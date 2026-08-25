from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, field_serializer

from app.schemas.common import CamelModel, to_rfc3339
from app.schemas.search import SearchScope


class SessionCreate(CamelModel):
    title: str = Field(default="Untitled", min_length=1, max_length=200)
    scope: SearchScope = Field(default_factory=lambda: SearchScope(type="all"))


class SessionOut(CamelModel):
    id: uuid.UUID
    title: str
    scope_type: str
    scope_payload: dict[str, object]
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def serialize_time(self, value: datetime) -> str | None:
        return to_rfc3339(value)


class MessageOut(CamelModel):
    id: uuid.UUID
    role: str
    status: str
    content: str
    citations: list[dict[str, object]] | None
    created_at: datetime

    @field_serializer("created_at")
    def serialize_time(self, value: datetime) -> str | None:
        return to_rfc3339(value)

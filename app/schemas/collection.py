from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, field_serializer

from app.schemas.common import CamelModel, to_rfc3339


class CollectionCreate(CamelModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)


class CollectionUpdate(CamelModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class CollectionOut(CamelModel):
    id: uuid.UUID
    name: str
    description: str
    document_count: int = 0
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def _ser_dt(self, value: datetime) -> str | None:
        return to_rfc3339(value)

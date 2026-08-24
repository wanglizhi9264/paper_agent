from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import field_serializer

from app.schemas.common import CamelModel, to_rfc3339


class JobOut(CamelModel):
    id: uuid.UUID
    document_id: uuid.UUID
    kind: str
    status: str
    stage: str
    progress: int
    attempt: int
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at", "started_at", "finished_at")
    def _ser_dt(self, value: datetime | None) -> str | None:
        return to_rfc3339(value)

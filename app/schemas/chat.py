from __future__ import annotations

import uuid

from pydantic import Field

from app.schemas.common import CamelModel


class ChatRequest(CamelModel):
    session_id: uuid.UUID
    query: str = Field(min_length=1, max_length=4000)


class ChatResponse(CamelModel):
    message_id: uuid.UUID
    answer: str
    citations: list[dict[str, object]]
    sources: list[dict[str, object]]
    rewritten_query: str
    degraded_reasons: list[str]

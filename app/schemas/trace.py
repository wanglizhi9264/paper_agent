from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, JsonValue

from app.agent.contracts import StrictModel
from app.schemas.chat import SourceOut


class TraceCandidate(StrictModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    section_path: list[str]
    page_start: int | None
    page_end: int | None
    raw_content: str
    retrieval_content: str
    raw_truncated: bool
    retrieval_truncated: bool


class TraceStep(StrictModel):
    sequence: int
    call_id: str
    tool: str
    status: str
    arguments: dict[str, JsonValue] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None
    error_code: str | None = None
    output: JsonValue = None
    retrieval: dict[str, JsonValue] | None = None
    candidates: list[TraceCandidate] = Field(default_factory=list)
    candidates_truncated: bool = False
    unavailable_candidates: int = 0


class TraceEvent(StrictModel):
    sequence: int
    type: str
    timestamp: datetime
    payload: dict[str, JsonValue]


class AgentTraceOut(StrictModel):
    schema_version: Literal[1] = 1
    run_id: uuid.UUID
    query: str
    status: str
    availability: Literal["recorded", "not_recorded", "expired"]
    created_at: datetime
    finished_at: datetime | None
    snapshot_id: uuid.UUID
    scope: dict[str, JsonValue]
    model_id: str
    context_limit: int | None
    loaded_skills: dict[str, str]
    usage: dict[str, int]
    stop_reason: str
    error_code: str | None
    answer: str | None
    steps: list[TraceStep]
    events: list[TraceEvent]
    sources: list[SourceOut]
    observed_evidence_count: int
    context: dict[str, JsonValue]

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.agent.contracts import Claim, StrictModel
from app.schemas.chat import SourceOut


class AgentRunCreate(StrictModel):
    session_id: uuid.UUID
    query: str = Field(min_length=1, max_length=4000)
    mode: Literal["agent"] = "agent"
    skill_names: list[str] = Field(default_factory=list, max_length=2)


class AgentRunOut(StrictModel):
    run_id: uuid.UUID
    session_id: uuid.UUID
    snapshot_id: uuid.UUID
    model_id: str
    status: Literal["queued", "running", "interrupted", "succeeded", "failed", "cancelled"]
    events_url: str
    usage: dict[str, int]
    selected_skills: dict[str, str]
    stop_reason: str
    error_code: str | None
    final_message_id: uuid.UUID | None
    answer: str | None
    sources: list[SourceOut]
    claims: list[Claim]
    steps: list[dict[str, Any]]
    created_at: datetime


class SkillOut(StrictModel):
    name: str
    description: str
    digest: str


class SummaryData(StrictModel):
    goal: str = ""
    selected_document_ids: list[uuid.UUID] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    comparison_axes: list[str] = Field(default_factory=list)
    user_constraints: list[str] = Field(default_factory=list)
    resolved_points: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    source_message_ids: list[uuid.UUID] = Field(default_factory=list)

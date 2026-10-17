from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.llm.base import LLMMessage
from app.schemas.chat import SourceOut


class AgentError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Action(StrictModel):
    tool: Literal[
        "list_papers",
        "search_papers",
        "read_chunks",
        "read_table",
        "load_skill",
        "read_skill_resource",
        "finish",
    ]
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    call_id: str = Field(default="", max_length=128)


class ListPapers(StrictModel):
    query: str = Field(default="", max_length=1000)
    limit: int = Field(default=20, ge=1, le=20)


class SearchPapers(StrictModel):
    query: str = Field(min_length=1, max_length=4000)
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    top_k: int = Field(default=6, ge=1, le=8)


class ReadChunks(StrictModel):
    chunk_ids: list[uuid.UUID] = Field(min_length=1, max_length=4)


class LoadSkill(StrictModel):
    name: str = Field(min_length=1, max_length=64)


class ReadTable(StrictModel):
    evidence_id: uuid.UUID
    cell_ids: list[uuid.UUID] = Field(default_factory=list, max_length=40)
    rows: list[int] = Field(default_factory=list, max_length=40)


class ReadResource(LoadSkill):
    relative_path: str = Field(min_length=1, max_length=200)


class Finish(StrictModel):
    pass  # Empty arguments are intentional: finalization is server controlled.


TOOL_INPUTS: dict[str, type[StrictModel]] = {
    "list_papers": ListPapers,
    "search_papers": SearchPapers,
    "read_chunks": ReadChunks,
    "read_table": ReadTable,
    "load_skill": LoadSkill,
    "read_skill_resource": ReadResource,
    "finish": Finish,
}


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    version: str
    description: str
    input_model: type[StrictModel]
    handler_key: str
    read_only: bool = True
    timeout_ms: int = 30000
    max_output_tokens: int = 2000

    def input_schema(self) -> dict[str, object]:
        return self.input_model.model_json_schema()

    def output_schema(self) -> dict[str, object]:
        return Observation.model_json_schema()


TOOL_REGISTRY = {
    name: ToolDefinition(name, "1", description, TOOL_INPUTS[name], handler)
    for name, description, handler in [
        (
            "list_papers",
            "List scoped paper titles. Query matches title keywords; use an empty query when the title is unknown.",
            "corpus.list",
        ),
        (
            "search_papers",
            "Search the existing hybrid index for original evidence; document IDs can only narrow scope.",
            "corpus.search",
        ),
        (
            "read_chunks",
            "Read up to four original excerpts by scoped chunk IDs to verify evidence.",
            "corpus.read",
        ),
        (
            "read_table",
            "Read original cells and headers from canonical IR. evidence_id is a returned chunk ID; rows start at zero. Select at most table_outline.max_rows_per_read relevant rows, not all baselines; output is limited to 40 cells.",
            "corpus.table",
        ),
        (
            "load_skill",
            "Activate a validated reading workflow without granting additional permissions.",
            "skills.load",
        ),
        (
            "read_skill_resource",
            "Read an allowed Markdown resource from an activated skill. Never execute scripts.",
            "skills.resource",
        ),
        (
            "finish",
            "Stop requesting tools; the server will generate and validate the answer.",
            "runtime.finish",
        ),
    ]
}


class Evidence(StrictModel):
    source: SourceOut
    document_version_id: uuid.UUID
    content_hash: str


class Claim(StrictModel):
    text: str
    source_ids: list[uuid.UUID]
    status: Literal["cited", "gap"]


class Observation(StrictModel):
    tool: str
    status: Literal["ok", "error"]
    data: JsonValue = None
    error_code: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class RuntimeState(StrictModel):
    schema_version: Literal[1] = 1
    phase: Literal["act", "tool", "finalize", "done"] = "act"
    action: Action | None = None
    observations: list[Observation] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    loaded_skills: dict[str, str] = Field(default_factory=dict)
    llm_calls: int = 0
    tool_calls: int = 0
    search_calls: int = 0
    actions: int = 0
    tokens: int = 0
    action_hashes: list[str] = Field(default_factory=list)
    stop_reason: str = "completed"
    answer: str | None = None
    citations: list[dict[str, JsonValue]] = Field(default_factory=list)
    packed_sources: list[SourceOut] = Field(default_factory=list)
    pending_actions: list[Action] = Field(default_factory=list)
    native_messages: list[dict[str, JsonValue]] = Field(default_factory=list)
    summary: dict[str, JsonValue] = Field(default_factory=dict)
    summary_attempted: bool = False
    partial_answer: str = ""
    attempt_id: str = ""
    completed_call_id: str = ""
    context_retry_used: bool = False
    claims: list[Claim] = Field(default_factory=list)


class TokenCounter(Protocol):
    signature: str

    def count(self, text: str) -> int: ...

    def count_messages(self, messages: list[LLMMessage]) -> int: ...

    def count_request(self, messages: list[LLMMessage], tools: list[dict[str, object]]) -> int: ...


class ToolBackend(Protocol):
    async def execute(self, action: Action) -> Observation: ...


class RuntimeStore(Protocol):
    async def save(self, state: RuntimeState, event: str) -> None: ...

    async def check(self) -> None: ...

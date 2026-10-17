from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent.context import fit_messages, pack_sources
from app.agent.contracts import (
    Action,
    AgentError,
    Evidence,
    Observation,
    RuntimeState,
    SearchPapers,
)
from app.agent.runtime import PaperAgent
from app.agent.skills import SkillRegistry
from app.llm.base import LLMMessage, LLMResponse, LLMUsage
from app.llm.openai_compatible import FakeLLMProvider
from app.schemas.chat import SourceOut


class FakeCounter:
    signature = "test-tokenizer"

    def count(self, text: str) -> int:
        return len(text)

    def count_messages(self, messages: list[LLMMessage]) -> int:
        return sum(len(m.content) + 4 for m in messages) + 2

    def count_request(self, messages, tools):
        return self.count_messages(messages) + self.count(str(tools))


class QuarterCounter(FakeCounter):
    def count(self, text: str) -> int:
        return (len(text) + 3) // 4

    def count_messages(self, messages: list[LLMMessage]) -> int:
        return sum(self.count(m.content) + 4 for m in messages) + 2


class MemoryStore:
    def __init__(self) -> None:
        self.states: list[RuntimeState] = []
        self.events: list[str] = []

    async def save(self, state: RuntimeState, event: str) -> None:
        self.states.append(copy.deepcopy(state))
        self.events.append(event)

    async def check(self) -> None:
        return None


class ScriptedLLM(FakeLLMProvider):
    def __init__(self, replies: list[str]) -> None:
        super().__init__()
        self.replies = iter(replies)
        self.messages: list[list[LLMMessage]] = []

    async def generate(self, messages, **kwargs) -> LLMResponse:
        self.messages.append(messages)
        return LLMResponse(next(self.replies), LLMUsage(total_tokens=100))


def evidence(text: str = "The method reduces training time.") -> Evidence:
    return Evidence(
        source=SourceOut(
            index=1,
            chunk_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            document_title="Paper",
            section_path=["Method"],
            page="1",
            page_start=1,
            page_end=1,
            content=text,
            truncated=False,
        ),
        document_version_id=uuid.uuid4(),
        content_hash="a" * 64,
    )


class Backend:
    def __init__(self, result: Evidence | None = None) -> None:
        self.calls: list[Action] = []
        self.result = result

    async def execute(self, action: Action) -> Observation:
        self.calls.append(action)
        return Observation(
            tool=action.tool, status="ok", data=[], evidence=[self.result] if self.result else []
        )


@pytest.fixture
def registry() -> SkillRegistry:
    return SkillRegistry(Path(__file__).resolve().parents[2] / "agent_skills")


def test_context_preserves_complete_turns_and_rejects_large_query() -> None:
    counter = FakeCounter()
    history = [
        LLMMessage("user", "old"),
        LLMMessage("assistant", "answer"),
        LLMMessage("user", "new"),
        LLMMessage("assistant", "reply"),
    ]
    result = fit_messages("system", "question", history, counter, 45)
    assert [(m.role, m.content) for m in result[1:-1]] == [("user", "new"), ("assistant", "reply")]
    assert counter.count_messages(result) <= 45
    with pytest.raises(AgentError, match="QUERY_TOO_LONG"):
        fit_messages("system", "a" * 801, [], counter, 900)
    with pytest.raises(AgentError, match="CONTEXT_BUDGET_EXCEEDED"):
        fit_messages("system", "question", [], counter, 10)


def test_pack_sources_deduplicates_and_counts_headers() -> None:
    item = evidence("First sentence. " * 100)
    sources = pack_sources([item, item], FakeCounter(), 220)
    assert len(sources) == 1
    assert sources[0].truncated
    assert sources[0].chunk_id == item.source.chunk_id
    assert sources[0].content.endswith(".")
    assert pack_sources([item], FakeCounter(), 2) == []


def test_tool_arguments_forbid_policy_override() -> None:
    with pytest.raises(ValidationError):
        SearchPapers.model_validate({"query": "method", "scope": {"type": "all"}})
    with pytest.raises(ValidationError):
        Action(tool="shell", arguments={})


def test_skill_registry_disclosure_and_path_policy(registry: SkillRegistry, tmp_path: Path) -> None:
    assert len(registry.skills) == 4
    assert registry.read("paper-reading").startswith("---")
    with pytest.raises(AgentError, match="SKILL_RESOURCE_INVALID"):
        registry.read("paper-reading", "../../.env")
    with pytest.raises(AgentError, match="SKILL_NOT_FOUND"):
        registry.get("missing")
    folder = tmp_path / "test-skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: test-skill\nname: test-skill\ndescription: test\n---\nbody", encoding="utf-8"
    )
    with pytest.raises(AgentError, match="SKILL_INVALID"):
        SkillRegistry(tmp_path)


async def test_real_action_tool_finalization_and_checkpoint(registry: SkillRegistry) -> None:
    provider = ScriptedLLM(
        [
            '{"tool":"search_papers","arguments":{"query":"method"}}',
            '{"tool":"finish","arguments":{}}',
            "Training is faster [1].",
        ]
    )
    backend, store = Backend(evidence()), MemoryStore()
    state = RuntimeState(loaded_skills={"paper-reading": registry.get("paper-reading").digest})
    result = await PaperAgent(provider, QuarterCounter(), backend, store, registry).run(
        state, "Explain the method", [], datetime.now(UTC) + timedelta(seconds=10)
    )
    assert result.phase == "done"
    assert len(backend.calls) == 1
    assert result.citations[0]["chunk_id"] == str(backend.result.source.chunk_id)
    assert result.packed_sources[0].content == backend.result.source.content
    assert "model.started" in store.events
    assert store.states[0].llm_calls == 1  # Reservation committed BEFORE I/O.
    assert "paper-reading" in provider.messages[-1][0].content


async def test_resume_after_completed_tool_does_not_repeat_it(registry: SkillRegistry) -> None:
    store, backend = MemoryStore(), Backend(evidence())
    state = RuntimeState(
        phase="act", tool_calls=1, search_calls=1, actions=1, evidence=[evidence()]
    )
    result = await PaperAgent(
        ScriptedLLM(['{"tool":"finish","arguments":{}}', "Result [1]."]),
        QuarterCounter(),
        backend,
        store,
        registry,
    ).run(state, "Question", [], datetime.now(UTC) + timedelta(seconds=10))
    assert backend.calls == []
    assert result.tool_calls == 1


async def test_invalid_citation_fails_closed(registry: SkillRegistry) -> None:
    state = RuntimeState(phase="finalize", evidence=[evidence()])
    with pytest.raises(AgentError, match="CITATION_INVALID"):
        await PaperAgent(
            ScriptedLLM(["Invented [99]."]), QuarterCounter(), Backend(), MemoryStore(), registry
        ).run(state, "Question", [], datetime.now(UTC) + timedelta(seconds=10))


async def test_empty_evidence_refuses_without_generation(registry: SkillRegistry) -> None:
    provider = ScriptedLLM(['{"tool":"finish","arguments":{}}'])
    result = await PaperAgent(provider, QuarterCounter(), Backend(), MemoryStore(), registry).run(
        RuntimeState(), "Question", [], datetime.now(UTC) + timedelta(seconds=10)
    )
    assert result.answer == "Insufficient evidence in the provided sources."
    assert len(provider.messages) == 1


async def test_repeated_action_stops_and_budget_not_reset(registry: SkillRegistry) -> None:
    action = '{"tool":"list_papers","arguments":{}}'
    result = await PaperAgent(
        ScriptedLLM([action, action]), QuarterCounter(), Backend(), MemoryStore(), registry
    ).run(RuntimeState(), "Question", [], datetime.now(UTC) + timedelta(seconds=10))
    assert result.stop_reason == "no_progress"
    assert result.tool_calls == 1
    assert result.llm_calls == 2


async def test_deadline_never_reset_on_resume(registry: SkillRegistry) -> None:
    with pytest.raises(AgentError, match="RUN_DEADLINE_EXCEEDED"):
        await PaperAgent(ScriptedLLM([]), QuarterCounter(), Backend(), MemoryStore(), registry).run(
            RuntimeState(), "Question", [], datetime.now(UTC) - timedelta(seconds=1)
        )

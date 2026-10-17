from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.agent.contracts import RuntimeState
from app.agent.runtime import PaperAgent
from app.agent.skills import SkillRegistry
from app.llm.base import LLMActionResponse, LLMError, LLMMessage
from app.llm.openai_compatible import FakeLLMProvider
from tests.unit.test_paper_agent import Backend, MemoryStore, QuarterCounter, evidence


class NativeProvider(FakeLLMProvider):
    def __init__(self):
        super().__init__(response_template="The method works [1].")
        self.requests = []

    async def generate_action(self, messages, tools, **kwargs):
        self.requests.append(messages)
        if len(self.requests) == 1:
            return LLMActionResponse(
                LLMMessage(
                    "assistant",
                    "",
                    tool_calls=[
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "search_papers",
                                "arguments": '{"query":"method"}',
                            },
                        }
                    ],
                )
            )
        return LLMActionResponse(LLMMessage("assistant", "Ready"))


async def test_native_tool_result_pairs_and_final_stream():
    provider = NativeProvider()
    store = MemoryStore()
    backend = Backend(evidence())
    agent = PaperAgent(
        provider,
        QuarterCounter(),
        backend,
        store,
        SkillRegistry(Path("agent_skills")),
        action_protocol="native",
        stream_answer=True,
    )
    result = await agent.run(
        RuntimeState(), "Question", [], datetime.now(UTC) + timedelta(seconds=10)
    )
    assert result.phase == "done"
    assert result.citations
    pair = provider.requests[1][-2:]
    assert pair[0].role == "assistant"
    assert pair[1].role == "tool"
    assert pair[1].tool_call_id == "call-1"
    assert result.completed_call_id == "call-1"
    assert "answer.started" in store.events
    assert "answer.delta" in store.events


class FailingSummary(FakeLLMProvider):
    async def generate(self, messages, **kwargs):
        raise LLMError("Provider unavailable")


async def test_summary_failure_preserves_deterministic_recent_history():
    agent = PaperAgent(
        FailingSummary(),
        QuarterCounter(),
        Backend(),
        MemoryStore(),
        SkillRegistry(Path("agent_skills")),
    )
    history = [
        LLMMessage("user", "question" * 400),
        LLMMessage("assistant", "answer" * 400),
        LLMMessage("user", "last"),
        LLMMessage("assistant", "reply"),
    ]
    state = RuntimeState()
    await agent.prepare_summary(state, history, "Current")
    assert state.summary_attempted
    assert not state.summary
    assert state.llm_calls == 1

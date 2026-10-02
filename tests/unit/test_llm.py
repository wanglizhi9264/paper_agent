from __future__ import annotations

import pytest

from app.llm.base import LLMError, LLMMessage
from app.llm.citations import (
    REFUSAL_PREFIX,
    RefusalStreamGate,
    finalize_answer,
    parse_citations,
    strip_invalid_markers,
    validate_citations,
)
from app.llm.openai_compatible import FakeLLMProvider, OpenAICompatibleProvider
from app.llm.prompts import build_messages, build_rewrite_prompt, build_system_prompt


@pytest.mark.asyncio
async def test_fake_llm_generate() -> None:
    provider = FakeLLMProvider()
    messages = [LLMMessage(role="user", content="test question")]
    resp = await provider.generate(messages)
    assert resp.text
    assert resp.finish_reason == "stop"
    assert resp.usage.total_tokens > 0


@pytest.mark.asyncio
async def test_fake_llm_stream() -> None:
    provider = FakeLLMProvider()
    messages = [LLMMessage(role="user", content="test question")]
    chunks = [c async for c in provider.stream(messages)]
    assert len(chunks) > 1
    assert chunks[-1].finish_reason == "stop"
    assert chunks[-1].usage is not None
    # Reconstruct text
    text = "".join(c.text for c in chunks)
    assert len(text) > 0


@pytest.mark.asyncio
async def test_fake_llm_custom_template() -> None:
    provider = FakeLLMProvider(response_template="Custom answer [1].")
    resp = await provider.generate([LLMMessage(role="user", content="q")])
    assert "Custom answer" in resp.text


@pytest.mark.asyncio
async def test_openai_adapter_normalizes_malformed_response(monkeypatch) -> None:
    import httpx

    class MalformedResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, *_args: object, **_kwargs: object) -> MalformedResponse:
            return MalformedResponse()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: Client())
    provider = OpenAICompatibleProvider("http://127.0.0.1:1/v1", "secret", "model")

    with pytest.raises(LLMError, match="request or response failed"):
        await provider.generate([LLMMessage(role="user", content="question")])


def test_build_system_prompt() -> None:
    sources = "[Source 1]\nDocument: Test\nContent: hello"
    prompt = build_system_prompt(sources)
    assert "Sources:" in prompt
    assert "[Source 1]" in prompt


def test_build_rewrite_prompt() -> None:
    history = [("user", "what is X"), ("assistant", "X is Y")]
    prompt = build_rewrite_prompt(history, "how does it work?")
    assert "standalone_query" in prompt
    assert "how does it work" in prompt


def test_build_messages() -> None:
    messages = build_messages("system prompt", [("user", "q1"), ("assistant", "a1")], "q2")
    assert len(messages) == 4
    assert messages[0].role == "system"
    assert messages[1].role == "user"
    assert messages[2].role == "assistant"
    assert messages[3].role == "user"
    assert messages[3].content == "q2"


def test_parse_citations_valid() -> None:
    text = "Answer [1] with detail [2]."
    cmap = {1: "chunk-a", 2: "chunk-b"}
    valid, invalid = parse_citations(text, cmap)
    assert len(valid) == 2
    assert valid[0].index == 1
    assert valid[0].chunk_id == "chunk-a"
    assert invalid == []


def test_parse_citations_invalid() -> None:
    text = "Answer [1] and [99]."
    cmap = {1: "chunk-a"}
    valid, invalid = parse_citations(text, cmap)
    assert len(valid) == 1
    assert valid[0].index == 1
    assert 99 in invalid


def test_parse_citations_repeated() -> None:
    text = "See [1] for details. Also [1] is important."
    cmap = {1: "chunk-a"}
    valid, invalid = parse_citations(text, cmap)
    assert len(valid) == 1  # deduplicated by index
    assert invalid == []


def test_strip_invalid_markers() -> None:
    text = "Answer [1] and [99] here."
    result = strip_invalid_markers(text, [99])
    assert "[99]" not in result
    assert "[1]" in result


def test_validate_citations_full() -> None:
    text = "Based on [1] and [99]."
    cmap = {1: "chunk-a"}
    cleaned, valid, invalid = validate_citations(text, cmap)
    assert "[99]" not in cleaned
    assert "[1]" in cleaned
    assert len(valid) == 1
    assert 99 in invalid


def test_parse_citations_no_markers() -> None:
    valid, invalid = parse_citations("no citations here", {1: "a"})
    assert valid == []
    assert invalid == []


def test_refusal_is_normalized_and_citations_are_cleared() -> None:
    answer, citations, invalid = finalize_answer(
        f"  {REFUSAL_PREFIX} A speculative explanation [1].", {1: "chunk-a"}
    )

    assert answer == REFUSAL_PREFIX
    assert citations == []
    assert invalid == []


def test_refusal_stream_gate_handles_split_prefix_and_discards_suffix() -> None:
    gate = RefusalStreamGate()

    assert gate.feed("Insufficient evidence in ") == ""
    assert gate.feed("the provided sources. Unsupported [1].") == REFUSAL_PREFIX
    assert gate.feed(" more") == ""
    assert gate.finish() == ""


def test_refusal_stream_gate_releases_non_refusal_text_losslessly() -> None:
    gate = RefusalStreamGate()

    first = gate.feed("Evidence [1]")
    second = gate.feed(" continues.")

    assert first + second + gate.finish() == "Evidence [1] continues."

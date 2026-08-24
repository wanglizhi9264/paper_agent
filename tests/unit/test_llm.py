from __future__ import annotations

import pytest

from app.llm.base import LLMMessage
from app.llm.citations import parse_citations, strip_invalid_markers, validate_citations
from app.llm.openai_compatible import FakeLLMProvider
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

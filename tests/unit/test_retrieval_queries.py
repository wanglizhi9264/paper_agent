from __future__ import annotations

import pytest

from app.llm.base import LLMError, LLMMessage, LLMResponse
from app.services.retrieval_queries import build_retrieval_queries, contains_cjk


class RecordingProvider:
    def __init__(self, text: str = "", *, error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[tuple[list[LLMMessage], dict[str, object]]] = []

    async def generate(self, messages: list[LLMMessage], **kwargs: object) -> LLMResponse:
        self.calls.append((messages, kwargs))
        if self.error is not None:
            raise self.error
        return LLMResponse(text=self.text)


def test_contains_cjk_is_narrow_and_deterministic() -> None:
    assert contains_cjk("比较 FID 与 CLIP score")
    assert not contains_cjk("Compare FID and CLIP score")


@pytest.mark.asyncio
async def test_off_mode_never_calls_llm_for_cjk() -> None:
    provider = RecordingProvider('{"english_query":"ignored"}')

    outcome = await build_retrieval_queries(provider, "比较模型 A 和 B", mode="off")

    assert outcome.queries.executed() == ["比较模型 A 和 B"]
    assert outcome.degraded_reasons == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_non_cjk_never_calls_llm_when_dual_mode_enabled() -> None:
    provider = RecordingProvider('{"english_query":"ignored"}')

    outcome = await build_retrieval_queries(
        provider,
        "Compare model A and B on FID",
        mode="cjk_to_english_dual",
    )

    assert outcome.queries.executed() == ["Compare model A and B on FID"]
    assert provider.calls == []


@pytest.mark.asyncio
async def test_cjk_success_keeps_original_and_adds_english_query() -> None:
    provider = RecordingProvider('{"english_query":"Compare model A and model B on FID = 12.4"}')

    outcome = await build_retrieval_queries(
        provider,
        "比较模型 A 和模型 B 的 FID = 12.4",
        mode="cjk_to_english_dual",
        reasoning_effort="none",
    )

    assert outcome.queries.executed() == [
        "比较模型 A 和模型 B 的 FID = 12.4",
        "Compare model A and model B on FID = 12.4",
    ]
    prompt = provider.calls[0][0][0].content
    assert "Do NOT answer" in prompt
    assert "acronym" in prompt
    assert "number" in prompt
    assert provider.calls[0][1]["temperature"] == 0.0
    assert provider.calls[0][1]["reasoning_effort"] == "none"


@pytest.mark.parametrize(
    "provider",
    [
        RecordingProvider("not json"),
        RecordingProvider('{"english_query":"仍然是中文"}'),
        RecordingProvider(error=LLMError("provider unavailable")),
    ],
)
@pytest.mark.asyncio
async def test_expected_translation_failures_fall_back_to_original(provider) -> None:
    outcome = await build_retrieval_queries(
        provider,
        "比较模型 A 和 B",
        mode="cjk_to_english_dual",
    )

    assert outcome.queries.executed() == ["比较模型 A 和 B"]
    assert outcome.degraded_reasons == ["CROSS_LANGUAGE_REWRITE_FAILED"]


@pytest.mark.asyncio
async def test_unexpected_programming_error_is_not_swallowed() -> None:
    provider = RecordingProvider(error=RuntimeError("bug"))

    with pytest.raises(RuntimeError, match="bug"):
        await build_retrieval_queries(
            provider,
            "比较模型 A 和 B",
            mode="cjk_to_english_dual",
        )

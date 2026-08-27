from __future__ import annotations

import pytest

from app.llm.base import LLMMessage, LLMResponse
from app.schemas.search import SearchScope
from app.services.query_rewrite import rewrite_query


class RewriteProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.messages: list[LLMMessage] = []

    async def generate(self, messages: list[LLMMessage], **_kwargs: object) -> LLMResponse:
        self.messages = messages
        return LLMResponse(text=self.text)


@pytest.mark.asyncio
async def test_eval_048_structured_rewrite_preserves_all_semantic_slots() -> None:
    provider = RewriteProvider(
        '{"standalone_query":"EEG2IM ImageNet-4 H+L+FiLM IS FID",'
        '"paper_hints":["EEG2IM"],"dataset_hints":["ImageNet-4"],'
        '"method_hints":["H+L+FiLM"],"metric_hints":["IS","FID"]}'
    )
    history = [
        ("user", "EEG2IM 在 ImageNet-4 上 H+L 的 IS 和 FID 是多少？"),
        ("assistant", "已有 H+L 的结果。"),
    ]

    result = await rewrite_query(
        provider, history, "那加上 FiLM 以后是多少？", SearchScope(type="all")
    )

    assert result.degraded_reasons == []
    assert result.rewrite.paper_hints == ["EEG2IM"]
    assert result.rewrite.dataset_hints == ["ImageNet-4"]
    assert result.rewrite.method_hints == ["H+L+FiLM"]
    assert result.rewrite.metric_hints == ["IS", "FID"]


@pytest.mark.asyncio
async def test_rewrite_uses_only_recent_four_messages_and_includes_scope() -> None:
    provider = RewriteProvider(
        '{"standalone_query":"q","paper_hints":[],"dataset_hints":[],'
        '"method_hints":[],"metric_hints":[]}'
    )
    history = [("user", f"secret-{index}") for index in range(6)]

    await rewrite_query(
        provider,
        history,
        "current",
        SearchScope(type="documents", document_ids=[__import__("uuid").uuid4()]),
    )

    prompt = provider.messages[0].content
    assert "secret-0" not in prompt
    assert "secret-1" not in prompt
    assert "secret-2" in prompt
    assert "secret-5" in prompt
    assert '"type":"documents"' in prompt
    assert "retrieval results" not in prompt.lower()


@pytest.mark.asyncio
async def test_invalid_rewrite_falls_back_to_original_query() -> None:
    provider = RewriteProvider("not json")

    result = await rewrite_query(
        provider, [("user", "context")], "original", SearchScope(type="all")
    )

    assert result.rewrite.standalone_query == "original"
    assert result.degraded_reasons == ["REWRITE_FAILED"]

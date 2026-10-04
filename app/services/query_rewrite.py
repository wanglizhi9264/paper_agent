"""Validated conversational query rewrite with an explicit safe fallback."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from app.llm.base import LLMError, LLMMessage, LLMProvider, ReasoningEffort
from app.llm.prompts import build_rewrite_prompt
from app.schemas.rewrite import StructuredRewrite
from app.schemas.search import SearchScope


@dataclass(frozen=True)
class RewriteOutcome:
    rewrite: StructuredRewrite
    degraded_reasons: list[str]


def _fallback(query: str) -> RewriteOutcome:
    return RewriteOutcome(
        rewrite=StructuredRewrite(standalone_query=query),
        degraded_reasons=["REWRITE_FAILED"],
    )


def _json_payload(text: str) -> object:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            stripped = "\n".join(lines[1:-1])
    return json.loads(stripped)


_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _should_rewrite_to_english(
    query: str, language_strategy: Literal["preserve", "english_for_cjk"]
) -> bool:
    return language_strategy == "english_for_cjk" and _CJK_PATTERN.search(query) is not None


async def rewrite_query(
    provider: LLMProvider,
    history: list[tuple[str, str]],
    query: str,
    scope: SearchScope,
    *,
    language_strategy: Literal["preserve", "english_for_cjk"] = "preserve",
    reasoning_effort: ReasoningEffort | None = None,
) -> RewriteOutcome:
    rewrite_to_english = _should_rewrite_to_english(query, language_strategy)
    if not history and not rewrite_to_english:
        return RewriteOutcome(StructuredRewrite(standalone_query=query), [])
    prompt = build_rewrite_prompt(
        history[-4:],
        query,
        scope.model_dump_json(),
        retrieval_language="english" if rewrite_to_english else "preserve",
    )
    try:
        response = await provider.generate(
            [LLMMessage(role="user", content=prompt)],
            temperature=0.0,
            max_tokens=500,
            reasoning_effort=reasoning_effort,
        )
        rewrite = StructuredRewrite.model_validate(_json_payload(response.text))
        return RewriteOutcome(rewrite=rewrite, degraded_reasons=[])
    except (LLMError, json.JSONDecodeError, ValidationError):
        return _fallback(query)

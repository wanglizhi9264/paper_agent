"""Validated conversational query rewrite with an explicit safe fallback."""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.llm.base import LLMMessage, LLMProvider
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


async def rewrite_query(
    provider: LLMProvider,
    history: list[tuple[str, str]],
    query: str,
    scope: SearchScope,
) -> RewriteOutcome:
    if not history:
        return RewriteOutcome(StructuredRewrite(standalone_query=query), [])
    prompt = build_rewrite_prompt(history[-4:], query, scope.model_dump_json())
    try:
        response = await provider.generate(
            [LLMMessage(role="user", content=prompt)], temperature=0.0, max_tokens=500
        )
        rewrite = StructuredRewrite.model_validate(_json_payload(response.text))
        return RewriteOutcome(rewrite=rewrite, degraded_reasons=[])
    except Exception:
        return _fallback(query)

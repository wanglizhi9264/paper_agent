"""Optional CJK-to-English retrieval query transformation.

Conversational standalone rewriting remains in ``query_rewrite``. This module
only creates an additional retrieval view and never changes scope or answer
generation language.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Literal

from pydantic import ValidationError

from app.llm.base import LLMError, LLMMessage, LLMProvider, ReasoningEffort
from app.llm.prompts import build_cross_language_retrieval_prompt
from app.schemas.rewrite import EnglishRetrievalQuery

CrossLanguageRetrievalMode = Literal["off", "cjk_to_english_dual"]

_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


@dataclass(frozen=True, slots=True)
class RetrievalQueries:
    original: str
    english: str | None = None
    mode: CrossLanguageRetrievalMode = "off"
    translation_attempted: bool = False

    def executed(self) -> list[str]:
        return [self.original] if self.english is None else [self.original, self.english]


@dataclass(frozen=True, slots=True)
class RetrievalQueryOutcome:
    queries: RetrievalQueries
    degraded_reasons: list[str]
    translation_ms: float


def contains_cjk(query: str) -> bool:
    return _CJK_PATTERN.search(query) is not None


def _json_payload(text: str) -> object:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            stripped = "\n".join(lines[1:-1])
    return json.loads(stripped)


async def build_retrieval_queries(
    provider: LLMProvider,
    standalone_query: str,
    *,
    mode: CrossLanguageRetrievalMode = "off",
    reasoning_effort: ReasoningEffort | None = None,
) -> RetrievalQueryOutcome:
    """Return original-only or original+English queries with a safe fallback."""
    original = standalone_query.strip()
    if mode == "off" or not contains_cjk(original):
        return RetrievalQueryOutcome(RetrievalQueries(original=original, mode=mode), [], 0.0)

    started = perf_counter()
    prompt = build_cross_language_retrieval_prompt(original)
    try:
        response = await provider.generate(
            [LLMMessage(role="user", content=prompt)],
            temperature=0.0,
            max_tokens=500,
            reasoning_effort=reasoning_effort,
        )
        translated = EnglishRetrievalQuery.model_validate(_json_payload(response.text))
        english = translated.english_query
        if english.casefold() == original.casefold() or contains_cjk(english):
            return RetrievalQueryOutcome(
                RetrievalQueries(
                    original=original,
                    mode=mode,
                    translation_attempted=True,
                ),
                ["CROSS_LANGUAGE_REWRITE_FAILED"],
                (perf_counter() - started) * 1000,
            )
        return RetrievalQueryOutcome(
            RetrievalQueries(
                original=original,
                english=english,
                mode=mode,
                translation_attempted=True,
            ),
            [],
            (perf_counter() - started) * 1000,
        )
    except (LLMError, json.JSONDecodeError, ValidationError):
        return RetrievalQueryOutcome(
            RetrievalQueries(
                original=original,
                mode=mode,
                translation_attempted=True,
            ),
            ["CROSS_LANGUAGE_REWRITE_FAILED"],
            (perf_counter() - started) * 1000,
        )

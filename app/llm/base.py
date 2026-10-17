from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

ReasoningEffort = Literal["none", "low", "high", "max"]


@dataclass
class LLMMessage:
    role: str  # "system" | "user" | "assistant"
    content: str
    tool_call_id: str | None = None
    tool_calls: list[dict[str, object]] = field(default_factory=list)


@dataclass
class LLMActionResponse:
    message: LLMMessage
    usage: LLMUsage = field(default_factory=lambda: LLMUsage())
    finish_reason: str = "stop"


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    text: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    finish_reason: str = "stop"


class LLMError(Exception):
    code = "LLM_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


@dataclass
class StreamChunk:
    text: str
    finish_reason: str | None = None
    usage: LLMUsage | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """Unified LLM protocol (spec §15).

    Business code depends only on this interface — never on a specific
    provider SDK. Provides async generate() and stream().
    """

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        timeout: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> LLMResponse: ...

    async def generate_action(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, object]],
        *,
        max_tokens: int = 600,
        timeout: float = 45,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> LLMActionResponse: ...

    def stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        timeout: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> AsyncIterator[StreamChunk]: ...

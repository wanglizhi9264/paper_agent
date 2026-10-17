from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, cast

from app.llm.base import (
    LLMActionResponse,
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMResponse,
    LLMUsage,
    ReasoningEffort,
    StreamChunk,
)


class FakeLLMProvider:
    """Deterministic fake LLM for CI/tests (spec §6).

    Produces a canned response with citations based on the system prompt's
    source markers. No network calls, no API key.
    """

    def __init__(self, response_template: str | None = None) -> None:
        self._template = response_template or (
            "Based on the provided sources [1], the answer is clear [2]. "
            "Additional context is available [1]."
        )

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        timeout: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> LLMResponse:
        return LLMResponse(
            text=self._template,
            usage=LLMUsage(
                prompt_tokens=sum(len(m.content) // 4 for m in messages),
                completion_tokens=len(self._template) // 4,
                total_tokens=(sum(len(m.content) for m in messages) + len(self._template)) // 4,
            ),
            finish_reason="stop",
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        timeout: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> AsyncIterator[StreamChunk]:
        words = self._template.split()
        for i, word in enumerate(words):
            sep = " " if i > 0 else ""
            yield StreamChunk(text=sep + word)
        yield StreamChunk(
            text="",
            finish_reason="stop",
            usage=LLMUsage(
                prompt_tokens=sum(len(m.content) // 4 for m in messages),
                completion_tokens=len(self._template) // 4,
                total_tokens=(sum(len(m.content) for m in messages) + len(self._template)) // 4,
            ),
        )

    async def generate_action(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, object]],
        *,
        max_tokens: int = 600,
        timeout: float = 45,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> LLMActionResponse:
        return LLMActionResponse(LLMMessage("assistant", ""))


class OpenAICompatibleProvider:
    """Real OpenAI-compatible provider using httpx (spec §15).

    Works with Ollama, vLLM, OpenAI, and any OpenAI-compatible endpoint.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        context_tokens: int = 8192,
        timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._context_tokens = context_tokens
        self._timeout = timeout

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        timeout: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> LLMResponse:
        import json

        import httpx

        try:
            payload = self._build_payload(
                messages,
                temperature,
                max_tokens,
                stream=False,
                reasoning_effort=reasoning_effort,
            )
            async with httpx.AsyncClient(timeout=timeout or self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                check_llm_status(resp)
                data = resp.json()
                choice = data["choices"][0]
                usage_data = data.get("usage", {})
                return LLMResponse(
                    text=choice["message"]["content"],
                    usage=LLMUsage(
                        prompt_tokens=usage_data.get("prompt_tokens", 0),
                        completion_tokens=usage_data.get("completion_tokens", 0),
                        total_tokens=usage_data.get("total_tokens", 0),
                    ),
                    finish_reason=choice.get("finish_reason", "stop"),
                )
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM request or response failed", code="LLM_UNAVAILABLE") from exc

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        timeout: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> AsyncIterator[StreamChunk]:
        import json

        import httpx

        try:
            payload = self._build_payload(
                messages, temperature, max_tokens, stream=True, reasoning_effort=reasoning_effort
            )
            async with (
                httpx.AsyncClient(timeout=timeout or self._timeout) as client,
                client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                ) as resp,
            ):
                check_llm_status(resp)
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    chunk_data = json.loads(data_str)
                    choices = chunk_data.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    text = delta.get("content") or ""
                    if not isinstance(text, str):
                        raise TypeError("Invalid stream content")
                    finish = choices[0].get("finish_reason")
                    usage = None
                    if "usage" in chunk_data:
                        u = chunk_data["usage"]
                        usage = LLMUsage(
                            prompt_tokens=u.get("prompt_tokens", 0),
                            completion_tokens=u.get("completion_tokens", 0),
                            total_tokens=u.get("total_tokens", 0),
                        )
                    yield StreamChunk(text=text, finish_reason=finish, usage=usage)
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM stream or response failed", code="LLM_UNAVAILABLE") from exc

    async def generate_action(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, object]],
        *,
        max_tokens: int = 600,
        timeout: float = 45,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> LLMActionResponse:
        import json

        # Native tool requests use the same explicitly configured reasoning mode.
        import httpx

        try:
            payload = self._build_payload(
                messages, 0.0, max_tokens, False, reasoning_effort=reasoning_effort
            )
            payload.update(tools=tools, tool_choice="auto", parallel_tool_calls=False)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                check_llm_status(response)
                data = response.json()
            choice = data["choices"][0]
            raw = choice["message"]
            calls = raw.get("tool_calls") or []
            if not isinstance(calls, list) or len(calls) > 10:
                raise LLMError("Invalid tool response", code="TOOL_ARGUMENT_INVALID")
            usage = data.get("usage") or {}
            return LLMActionResponse(
                LLMMessage("assistant", raw.get("content") or "", tool_calls=calls),
                LLMUsage(
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    usage.get("total_tokens", 0),
                ),
                choice.get("finish_reason", "stop"),
            )
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("Tool request failed", code="LLM_UNAVAILABLE") from exc

    def _build_payload(
        self,
        messages: list[LLMMessage],
        temperature: float,
        max_tokens: int | None,
        stream: bool,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [serialize_message(m) for m in messages],
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if reasoning_effort is not None:
            payload["reasoning_effort"] = reasoning_effort
        return payload


def serialize_message(message: LLMMessage) -> dict[str, Any]:
    result: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_call_id is not None:
        result["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        result["tool_calls"] = message.tool_calls
    return result


def check_llm_status(response: Any) -> None:
    if getattr(response, "status_code", None) == 400:
        try:
            error = response.json().get("error", {})
            if error.get("code") in {"context_length_exceeded", "context_window_exceeded"}:
                raise LLMError("Context limit exceeded", code="LLM_CONTEXT_OVERFLOW")
        except ValueError:
            pass
    response.raise_for_status()


def get_llm_provider(settings: Any | None = None) -> LLMProvider:
    """Return a cached LLM provider. Fake in test env, real in production."""
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    if settings.env == "test" or settings.llm_model == "fake":
        return cast(LLMProvider, FakeLLMProvider())
    return cast(
        LLMProvider,
        OpenAICompatibleProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            context_tokens=settings.llm_context_tokens,
            timeout=settings.llm_request_timeout_seconds,
        ),
    )

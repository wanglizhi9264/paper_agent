from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.gpu import acquire_gpu_slot
from app.llm.base import (
    LLMActionResponse,
    LLMMessage,
    LLMProvider,
    LLMResponse,
    ReasoningEffort,
    StreamChunk,
)


class GpuBoundProvider:
    """Coordinate runtime model calls with API/worker retrieval and ingestion."""

    def __init__(self, provider: LLMProvider, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.provider, self.sessions = provider, sessions

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        timeout: float | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> LLMResponse:
        async with self.sessions() as db, db.begin():
            await acquire_gpu_slot(db)
            return await self.provider.generate(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                reasoning_effort=reasoning_effort,
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
        async with self.sessions() as db, db.begin():
            await acquire_gpu_slot(db)
            return await self.provider.generate_action(
                messages,
                tools,
                max_tokens=max_tokens,
                timeout=timeout,
                reasoning_effort=reasoning_effort,
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
        async with self.sessions() as db, db.begin():
            await acquire_gpu_slot(db)
            async for chunk in self.provider.stream(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                reasoning_effort=reasoning_effort,
            ):
                yield chunk

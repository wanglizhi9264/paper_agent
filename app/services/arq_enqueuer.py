from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class ArqEnqueuer:
    """Enqueue jobs into ARQ (Redis). Recoverable via reconciliation on failure."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._redis_settings = _build_redis_settings(settings.redis_url)

    async def enqueue(
        self,
        job_id: str,
        kind: str,
        *,
        document_id: str,
        attempt: int = 1,
        extra: Mapping[str, object] | None = None,
    ) -> None:
        redis = await create_pool(self._redis_settings)
        try:
            await redis.enqueue_job(
                f"ingestion:{kind}",
                job_id,
                document_id=document_id,
                attempt=attempt,
                _extras=dict(extra) if extra else {},
            )
        finally:
            await redis.aclose()
        logger.info("job_enqueued", job_id=job_id, kind=kind, document_id=document_id)


def _build_redis_settings(url: str) -> RedisSettings:
    # arq RedisSettings parses host/port/db/password from a URL.
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return RedisSettings(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 6379,
        password=parsed.password,
        database=int((parsed.path or "/0").lstrip("/") or 0),
    )


class FakeEnqueuer:
    """In-memory enqueuer for tests. Records calls without dispatching."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue(
        self,
        job_id: str,
        kind: str,
        *,
        document_id: str,
        attempt: int = 1,
        extra: Mapping[str, object] | None = None,
    ) -> None:
        self.calls.append(
            {
                "job_id": job_id,
                "kind": kind,
                "document_id": document_id,
                "attempt": attempt,
                "extra": dict(extra) if extra else {},
            }
        )


# FakeEnqueuer satisfies TaskEnqueuer structurally; verified by static checks
# in the test suite that exercise it through DocumentService.

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable


@runtime_checkable
class TaskEnqueuer(Protocol):
    """Abstraction over enqueueing a background job.

    Decouples services from ARQ so unit tests can inject a fake. Implementations
    must be idempotent-ish: a failed enqueue is recovered by reconciliation,
    not by re-enqueuing blindly in the request path.
    """

    async def enqueue(
        self,
        job_id: str,
        kind: str,
        *,
        document_id: str,
        attempt: int = 1,
        extra: Mapping[str, object] | None = None,
    ) -> None: ...

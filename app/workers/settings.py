from __future__ import annotations

from typing import Any, ClassVar

from app.core.config import get_settings
from app.core.logging import configure_logging


class WorkerSettings:
    """ARQ worker settings. Tasks are registered in later phases."""

    functions: ClassVar[list[Any]] = []
    max_jobs: ClassVar[int] = 1
    job_timeout: ClassVar[int] = 1800
    retry_jobs: ClassVar[bool] = True
    max_tries: ClassVar[int] = 5

    async def on_startup(self, worker: Any) -> None:  # pragma: no cover
        settings = get_settings()
        configure_logging(settings.log_level)

    async def on_shutdown(self, worker: Any) -> None:  # pragma: no cover
        pass

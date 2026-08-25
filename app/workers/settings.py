from __future__ import annotations

from typing import Any, ClassVar

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.workers.tasks import functions as _task_functions


class WorkerSettings:
    """ARQ worker settings."""

    functions: ClassVar[list[Any]] = list(_task_functions)
    redis_settings: ClassVar[RedisSettings] = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs: ClassVar[int] = 1
    job_timeout: ClassVar[int] = 1800
    retry_jobs: ClassVar[bool] = True
    max_tries: ClassVar[int] = 5

    @staticmethod
    async def on_startup(ctx: dict[str, Any]) -> None:  # pragma: no cover
        settings = get_settings()
        configure_logging(settings.log_level)

    @staticmethod
    async def on_shutdown(ctx: dict[str, Any]) -> None:  # pragma: no cover
        pass

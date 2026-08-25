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
        del ctx
        settings = get_settings()
        configure_logging(settings.log_level)
        from app.db.session import session_scope
        from app.services.consistency import reconcile_stale_jobs, reconcile_v2_builds
        from app.services.ir_artifacts import IRArtifactManager

        async with session_scope() as session:
            await reconcile_stale_jobs(session)
            await reconcile_v2_builds(session, IRArtifactManager(settings.storage_dir))

    @staticmethod
    async def on_shutdown(ctx: dict[str, Any]) -> None:  # pragma: no cover
        del ctx

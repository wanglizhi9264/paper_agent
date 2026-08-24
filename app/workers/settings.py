from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from arq import Worker

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.workers.tasks import functions as _task_functions


class WorkerSettings:
    """ARQ worker settings."""

    functions: ClassVar[list[Any]] = list(_task_functions)
    max_jobs: ClassVar[int] = 1
    job_timeout: ClassVar[int] = 1800
    retry_jobs: ClassVar[bool] = True
    max_tries: ClassVar[int] = 5

    async def on_startup(self, worker: Worker) -> None:  # pragma: no cover
        settings = get_settings()
        configure_logging(settings.log_level)

    async def on_shutdown(self, worker: Worker) -> None:  # pragma: no cover
        pass

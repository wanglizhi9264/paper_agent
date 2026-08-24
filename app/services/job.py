from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import NotFoundError
from app.models.job import IngestionJob


class JobService:
    async def get(self, session: AsyncSession, job_id: uuid.UUID) -> IngestionJob:
        job = await session.get(IngestionJob, job_id)
        if job is None:
            raise NotFoundError(code="JOB_NOT_FOUND", message="Job was not found.")
        return job

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_session
from app.models.enums import DocumentStatus
from app.models.job import IngestionJob
from app.schemas.document import DocumentCreateResponse
from app.schemas.job import JobOut
from app.services.arq_enqueuer import ArqEnqueuer
from app.services.document import DocumentService
from app.services.job import JobService

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _job_service() -> JobService:
    return JobService()


def _to_out(j: IngestionJob) -> JobOut:
    return JobOut(
        id=j.id,
        document_id=j.document_id,
        kind=j.kind.value if hasattr(j.kind, "value") else str(j.kind),
        status=j.status.value if hasattr(j.status, "value") else str(j.status),
        stage=j.stage.value if hasattr(j.stage, "value") else str(j.stage),
        progress=j.progress,
        attempt=j.attempt,
        error_code=j.error_code,
        error_message=j.error_message,
        started_at=j.started_at,
        finished_at=j.finished_at,
        created_at=j.created_at,
        updated_at=j.updated_at,
    )


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    session: Annotated[AsyncSession, Depends(get_session)],
    job_id: uuid.UUID,
) -> JobOut:
    job = await _job_service().get(session, job_id)
    return _to_out(job)


@router.post("/{job_id}/retry", response_model=DocumentCreateResponse, status_code=202)
async def retry_job(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    job_id: uuid.UUID,
) -> DocumentCreateResponse:
    settings = get_settings()
    service = DocumentService(settings, ArqEnqueuer(settings))
    new_job = await service.retry_job(session, job_id)
    request.state.pending_enqueue = (
        str(new_job.id),
        new_job.kind.value,
        str(new_job.document_id),
        new_job.attempt,
    )
    return DocumentCreateResponse(
        document_id=new_job.document_id,
        job_id=new_job.id,
        status=DocumentStatus.QUEUED.value,
    )

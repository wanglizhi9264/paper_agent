from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import NotFoundError
from app.core.config import get_settings
from app.db.session import get_session
from app.schemas.evidence import (
    EvidenceChunks,
    EvidenceOperationOut,
    EvidenceResponse,
    EvidenceSearch,
    EvidenceTable,
    PaperList,
    PaperListRequest,
)
from app.services import evidence as service
from app.services.evidence_queue import dispatch_evidence


def require_enabled() -> None:
    if not get_settings().mcp_enabled:
        raise NotFoundError(
            code="MCP_DISABLED", message="Enable PAPER_RAG_MCP_ENABLED on the backend."
        )


router = APIRouter(
    prefix="/api/v1/evidence", tags=["evidence"], dependencies=[Depends(require_enabled)]
)
Database = Annotated[AsyncSession, Depends(get_session)]


@router.post("/papers", response_model=PaperList)
async def list_papers(body: PaperListRequest, db: Database) -> PaperList:
    return await service.list_papers(db, body)


@router.post("/search", response_model=EvidenceOperationOut, status_code=202)
async def search_papers(
    body: EvidenceSearch,
    db: Database,
    background: BackgroundTasks,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
) -> EvidenceOperationOut:
    op = await service.create_search(db, body, idempotency_key)
    result = service.operation_out(op)
    await db.commit()
    background.add_task(dispatch_evidence, {})
    return result


@router.get("/operations/{operation_id}", response_model=EvidenceOperationOut)
async def operation(operation_id: uuid.UUID, db: Database) -> EvidenceOperationOut:
    return service.operation_out(await service.get_operation(db, operation_id))


@router.post("/operations/{operation_id}/cancel", response_model=EvidenceOperationOut)
async def cancel(operation_id: uuid.UUID, db: Database) -> EvidenceOperationOut:
    return service.operation_out(
        await service.get_operation(db, operation_id, cancel=True), include_result=False
    )


@router.post("/chunks", response_model=EvidenceResponse)
async def read_chunks(body: EvidenceChunks, db: Database) -> EvidenceResponse:
    return await service.read_chunks(db, body)


@router.post("/table", response_model=EvidenceResponse)
async def read_table(body: EvidenceTable, db: Database) -> EvidenceResponse:
    return await service.read_table(db, body, get_settings())

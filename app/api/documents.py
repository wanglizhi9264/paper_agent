from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import request_id_dep
from app.api.errors import UnsupportedMediaTypeError
from app.core.config import get_settings
from app.db.session import get_session
from app.models.collection import CollectionDocument
from app.models.document import Document
from app.models.enums import DocumentStatus, JobStatus
from app.models.job import IngestionJob
from app.schemas.common import Page
from app.schemas.document import ChunkOut, DocumentCreateResponse, DocumentOut
from app.services.arq_enqueuer import ArqEnqueuer
from app.services.document import DocumentService

router = APIRouter(prefix="/api/v1/documents", tags=["documents"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def _service() -> DocumentService:
    settings = get_settings()
    return DocumentService(settings, ArqEnqueuer(settings))


def _cursor_id(raw: str | None) -> uuid.UUID | None:
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError):
        return None


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


async def _collection_ids(session: AsyncSession, document_id: uuid.UUID) -> list[uuid.UUID]:
    stmt = select(CollectionDocument.collection_id).where(
        CollectionDocument.document_id == document_id
    )
    return list((await session.execute(stmt)).scalars().all())


async def _active_job_id(session: AsyncSession, document_id: uuid.UUID) -> uuid.UUID | None:
    stmt = (
        select(IngestionJob.id)
        .where(
            IngestionJob.document_id == document_id,
            IngestionJob.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)),
        )
        .order_by(IngestionJob.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


def _to_out(
    session_doc: Document, cids: list[uuid.UUID], active_job: uuid.UUID | None
) -> DocumentOut:
    d = session_doc
    status = d.status.value if isinstance(d.status, DocumentStatus) else str(d.status)
    return DocumentOut(
        id=d.id,
        filename=d.filename,
        media_type=d.media_type,
        extension=d.extension,
        title=d.title,
        sha256=d.sha256,
        file_size=d.file_size,
        status=status,
        status_message=d.status_message,
        page_count=d.page_count,
        character_count=d.character_count,
        chunk_count=d.chunk_count,
        active_document_version_id=d.active_document_version_id,
        parser_version=d.parser_version,
        collection_ids=cids,
        active_job_id=active_job,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


@router.post("", response_model=DocumentCreateResponse, status_code=202)
async def create_document(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File()],
    collection_ids: Annotated[list[uuid.UUID] | None, Form()] = None,
    _: Annotated[str, Depends(request_id_dep)] = "",
) -> DocumentCreateResponse:
    if file.filename is None:
        raise UnsupportedMediaTypeError(
            code="UNSUPPORTED_MEDIA_TYPE", message="Upload must include a filename."
        )
    service = _service()
    doc, job = await service.ingest_upload(
        session,
        filename=file.filename,
        stream=file.file,
        content_type=file.content_type,
        collection_ids=collection_ids or [],
    )
    # Enqueue is dispatched from the response-sent hook after commit.
    request.state.pending_enqueue = (
        str(job.id),
        job.kind.value,
        str(job.document_id),
        job.attempt,
    )
    return DocumentCreateResponse(
        document_id=doc.id, job_id=job.id, status=DocumentStatus.QUEUED.value
    )


@router.get("", response_model=Page[DocumentOut])
async def list_documents(
    session: Annotated[AsyncSession, Depends(get_session)],
    status: Annotated[str | None, Query()] = None,
    collection_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[DocumentOut]:
    service = _service()
    docs, has_more = await service.list_documents(
        session,
        status=status,
        collection_id=collection_id,
        limit=_clamp_limit(limit),
        cursor_id=_cursor_id(cursor),
    )
    items: list[DocumentOut] = []
    for d in docs:
        cids = await _collection_ids(session, d.id)
        active_job = await _active_job_id(session, d.id)
        items.append(_to_out(d, cids, active_job))
    next_cursor = str(items[-1].id) if has_more and items else None
    return Page(items=items, next_cursor=next_cursor, has_more=has_more)


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    session: Annotated[AsyncSession, Depends(get_session)],
    document_id: uuid.UUID,
) -> DocumentOut:
    service = _service()
    d = await service.get_document(session, document_id)
    cids = await _collection_ids(session, d.id)
    active_job = await _active_job_id(session, d.id)
    return _to_out(d, cids, active_job)


@router.get("/{document_id}/chunks", response_model=Page[ChunkOut])
async def list_chunks(
    session: Annotated[AsyncSession, Depends(get_session)],
    document_id: uuid.UUID,
    kind: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[ChunkOut]:
    service = _service()
    cursor_index = int(cursor) if cursor and cursor.lstrip("-").isdigit() else None
    chunks, has_more = await service.get_chunks(
        session, document_id, kind=kind, limit=_clamp_limit(limit), cursor_index=cursor_index
    )
    items = [
        ChunkOut(
            id=c.id,
            document_id=c.document_id,
            document_version_id=c.document_version_id,
            chunk_index=c.chunk_index,
            kind=c.kind,
            parent_chunk_id=c.parent_chunk_id,
            chapter_chunk_id=c.chapter_chunk_id,
            section_path=c.section_path,
            page_start=c.page_start,
            page_end=c.page_end,
            line_start=c.line_start,
            line_end=c.line_end,
            raw_content=c.raw_content,
            retrieval_content=c.retrieval_content,
            content_hash=c.content_hash,
            character_count=c.character_count,
            token_count=c.token_count,
            created_at=c.created_at,
        )
        for c in chunks
    ]
    next_cursor = str(items[-1].chunk_index) if has_more and items else None
    return Page(items=items, next_cursor=next_cursor, has_more=has_more)


@router.post("/{document_id}/reindex", response_model=DocumentCreateResponse, status_code=202)
async def reindex_document(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    document_id: uuid.UUID,
) -> DocumentCreateResponse:
    service = _service()
    job = await service.request_reindex(session, document_id)
    request.state.pending_enqueue = (
        str(job.id),
        job.kind.value,
        str(job.document_id),
        job.attempt,
    )
    return DocumentCreateResponse(
        document_id=document_id, job_id=job.id, status=DocumentStatus.QUEUED.value
    )


@router.delete("/{document_id}", response_model=DocumentCreateResponse, status_code=202)
async def delete_document(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    document_id: uuid.UUID,
) -> DocumentCreateResponse:
    service = _service()
    job = await service.request_delete(session, document_id)
    request.state.pending_enqueue = (
        str(job.id),
        job.kind.value,
        str(job.document_id),
        job.attempt,
    )
    return DocumentCreateResponse(
        document_id=document_id, job_id=job.id, status=DocumentStatus.DELETING.value
    )

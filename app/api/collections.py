from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.collection import CollectionCreate, CollectionOut, CollectionUpdate
from app.schemas.common import Page
from app.services.collection import CollectionService

router = APIRouter(prefix="/api/v1/collections", tags=["collections"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def _service() -> CollectionService:
    return CollectionService()


def _cursor_id(raw: str | None) -> uuid.UUID | None:
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError):
        return None


@router.post("", response_model=CollectionOut, status_code=201)
async def create_collection(
    session: Annotated[AsyncSession, Depends(get_session)],
    payload: CollectionCreate,
) -> CollectionOut:
    coll = await _service().create(session, payload)
    count = await _service().document_count(session, coll.id)
    return CollectionOut(
        id=coll.id,
        name=coll.name,
        description=coll.description,
        document_count=count,
        created_at=coll.created_at,
        updated_at=coll.updated_at,
    )


@router.get("", response_model=Page[CollectionOut])
async def list_collections(
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[CollectionOut]:
    service = _service()
    colls, has_more = await service.list(
        session, limit=max(1, min(limit, MAX_LIMIT)), cursor_id=_cursor_id(cursor)
    )
    items: list[CollectionOut] = []
    for c in colls:
        count = await service.document_count(session, c.id)
        items.append(
            CollectionOut(
                id=c.id,
                name=c.name,
                description=c.description,
                document_count=count,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
        )
    next_cursor = str(items[-1].id) if has_more and items else None
    return Page(items=items, next_cursor=next_cursor, has_more=has_more)


@router.get("/{collection_id}", response_model=CollectionOut)
async def get_collection(
    session: Annotated[AsyncSession, Depends(get_session)],
    collection_id: uuid.UUID,
) -> CollectionOut:
    service = _service()
    c = await service.get(session, collection_id)
    count = await service.document_count(session, c.id)
    return CollectionOut(
        id=c.id,
        name=c.name,
        description=c.description,
        document_count=count,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


@router.patch("/{collection_id}", response_model=CollectionOut)
async def update_collection(
    session: Annotated[AsyncSession, Depends(get_session)],
    collection_id: uuid.UUID,
    payload: CollectionUpdate,
) -> CollectionOut:
    service = _service()
    c = await service.update(session, collection_id, payload)
    count = await service.document_count(session, c.id)
    return CollectionOut(
        id=c.id,
        name=c.name,
        description=c.description,
        document_count=count,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


@router.delete("/{collection_id}", status_code=204)
async def delete_collection(
    session: Annotated[AsyncSession, Depends(get_session)],
    collection_id: uuid.UUID,
) -> Response:
    await _service().delete(session, collection_id)
    return Response(status_code=204)


@router.put("/{collection_id}/documents/{document_id}", status_code=204)
async def add_document_to_collection(
    session: Annotated[AsyncSession, Depends(get_session)],
    collection_id: uuid.UUID,
    document_id: uuid.UUID,
) -> Response:
    await _service().add_document(session, collection_id, document_id)
    return Response(status_code=204)


@router.delete("/{collection_id}/documents/{document_id}", status_code=204)
async def remove_document_from_collection(
    session: Annotated[AsyncSession, Depends(get_session)],
    collection_id: uuid.UUID,
    document_id: uuid.UUID,
) -> Response:
    await _service().remove_document(session, collection_id, document_id)
    return Response(status_code=204)

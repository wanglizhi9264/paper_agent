from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ConflictError, NotFoundError
from app.models.collection import Collection, CollectionDocument
from app.models.document import Document
from app.schemas.collection import CollectionCreate, CollectionUpdate


class CollectionService:
    async def create(self, session: AsyncSession, payload: CollectionCreate) -> Collection:
        existing = await session.execute(select(Collection).where(Collection.name == payload.name))
        if existing.scalar_one_or_none() is not None:
            raise ConflictError(
                code="COLLECTION_NAME_TAKEN", message="Collection name already exists."
            )
        coll = Collection(name=payload.name, description=payload.description)
        session.add(coll)
        await session.flush()
        return coll

    async def get(self, session: AsyncSession, cid: uuid.UUID) -> Collection:
        coll = await session.get(Collection, cid)
        if coll is None:
            raise NotFoundError(code="COLLECTION_NOT_FOUND", message="Collection was not found.")
        return coll

    async def list(
        self, session: AsyncSession, *, limit: int, cursor_id: uuid.UUID | None
    ) -> tuple[list[Collection], bool]:
        stmt = select(Collection).order_by(Collection.created_at.desc(), Collection.id.desc())
        if cursor_id is not None:
            stmt = stmt.where(Collection.id < cursor_id)
        stmt = stmt.limit(limit + 1)
        rows = (await session.execute(stmt)).scalars().all()
        return list(rows[:limit]), len(rows) > limit

    async def update(
        self, session: AsyncSession, cid: uuid.UUID, payload: CollectionUpdate
    ) -> Collection:
        coll = await self.get(session, cid)
        if payload.name is not None and payload.name != coll.name:
            dup = await session.execute(select(Collection).where(Collection.name == payload.name))
            if dup.scalar_one_or_none() is not None:
                raise ConflictError(
                    code="COLLECTION_NAME_TAKEN", message="Collection name already exists."
                )
            coll.name = payload.name
        if payload.description is not None:
            coll.description = payload.description
        await session.flush()
        return coll

    async def delete(self, session: AsyncSession, cid: uuid.UUID) -> None:
        coll = await self.get(session, cid)
        await session.delete(coll)

    async def add_document(
        self, session: AsyncSession, cid: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        await self.get(session, cid)
        doc = await session.get(Document, document_id)
        if doc is None:
            raise NotFoundError(code="DOCUMENT_NOT_FOUND", message="Document was not found.")
        existing = await session.execute(
            select(CollectionDocument).where(
                CollectionDocument.collection_id == cid,
                CollectionDocument.document_id == document_id,
            )
        )
        if existing.scalar_one_or_none() is None:
            session.add(CollectionDocument(collection_id=cid, document_id=document_id))

    async def remove_document(
        self, session: AsyncSession, cid: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        await self.get(session, cid)
        existing = await session.execute(
            select(CollectionDocument).where(
                CollectionDocument.collection_id == cid,
                CollectionDocument.document_id == document_id,
            )
        )
        link = existing.scalar_one_or_none()
        if link is not None:
            await session.delete(link)

    async def document_count(self, session: AsyncSession, cid: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(CollectionDocument)
            .where(CollectionDocument.collection_id == cid)
        )
        return int((await session.execute(stmt)).scalar_one())

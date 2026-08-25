from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.embedding.registry import get_embedding_provider
from app.schemas.search import SearchRequest, SearchResponse
from app.services.retrieval import search_corpus

router = APIRouter(prefix="/api/v1/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SearchResponse:
    return await search_corpus(session, request, get_embedding_provider())

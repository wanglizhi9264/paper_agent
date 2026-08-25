from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text

from app.core.logging import get_logger
from app.db.session import get_engine, get_sessionmaker
from app.models.index_snapshot import IndexSnapshot, SystemState

router = APIRouter(prefix="/health", tags=["health"])
logger = get_logger(__name__)

_HealthStatus = Literal["ok", "degraded", "down"]


class ComponentHealth(BaseModel):
    status: _HealthStatus
    detail: str | None = None


class LiveResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyResponse(BaseModel):
    status: _HealthStatus
    components: dict[str, ComponentHealth]


async def _check_postgres() -> ComponentHealth:
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return ComponentHealth(status="ok")
    except Exception as exc:
        return ComponentHealth(status="down", detail=type(exc).__name__)


async def _check_redis() -> ComponentHealth:
    try:
        import redis.asyncio as aioredis  # local import to keep import cost off API startup

        from app.core.config import get_settings

        settings = get_settings()
        client = aioredis.from_url(settings.redis_url, socket_connect_timeout=2.0)  # type: ignore[no-untyped-call]
        try:
            pong = await asyncio.wait_for(client.ping(), timeout=2.0)
            if not pong:
                return ComponentHealth(status="down", detail="no pong")
        finally:
            await client.aclose()
        return ComponentHealth(status="ok")
    except Exception as exc:
        return ComponentHealth(status="down", detail=type(exc).__name__)


async def _check_index_snapshot() -> ComponentHealth:
    """Validate the active DB pointer and both durable index artifacts."""
    try:
        async with get_sessionmaker()() as session:
            state = await session.get(SystemState, 1)
            if state is None or state.active_index_snapshot_id is None:
                return ComponentHealth(status="ok", detail="not_initialized")
            snapshot = await session.get(IndexSnapshot, state.active_index_snapshot_id)
            if snapshot is None or not snapshot.faiss_path or not snapshot.bm25_path:
                return ComponentHealth(status="down", detail="invalid_active_snapshot")
            if not Path(snapshot.faiss_path).is_file() or not Path(snapshot.bm25_path).is_file():
                return ComponentHealth(status="down", detail="index_artifact_missing")
            return ComponentHealth(status="ok", detail=str(snapshot.id))
    except Exception as exc:
        return ComponentHealth(status="down", detail=type(exc).__name__)


@router.get("/live", response_model=LiveResponse)
async def live() -> LiveResponse:
    return LiveResponse()


@router.get("/ready", response_model=ReadyResponse)
async def ready(response: Response) -> ReadyResponse:
    pg, redis_h, index = await asyncio.gather(
        _check_postgres(),
        _check_redis(),
        _check_index_snapshot(),
    )
    components = {"postgres": pg, "redis": redis_h, "index": index}
    if any(c.status == "down" for c in components.values()):
        overall: _HealthStatus = "down"
        response.status_code = 503
    elif any(c.status == "degraded" for c in components.values()):
        overall = "degraded"
        response.status_code = 503
    else:
        overall = "ok"
    logger.info("health_ready", status=overall, components=components)
    return ReadyResponse(status=overall, components=components)

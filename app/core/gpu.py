from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

GPU_LOCK = 731946218005


async def acquire_gpu_slot(session: AsyncSession) -> None:
    """Cross-process transaction-scoped GPU gate; SQLite fakes need no GPU."""
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": GPU_LOCK})

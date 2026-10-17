from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from arq.connections import create_pool
from redis.exceptions import RedisError
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_sessionmaker
from app.models.evidence import EvidenceOperation
from app.services import evidence as service

logger = get_logger(__name__)


async def dispatch_evidence(ctx: dict[str, Any]) -> None:
    sessions = get_sessionmaker()
    now = datetime.now(UTC)
    async with sessions() as db, db.begin():
        await service.cleanup_contexts(db)
        ops = list(
            (
                await db.execute(
                    select(EvidenceOperation)
                    .where(EvidenceOperation.status.in_(["queued", "running"]))
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        pending: list[tuple[str, int]] = []
        for op in ops:
            if service.utc(op.deadline) <= now:
                op.status, op.error_code, op.finished_at = (
                    "failed",
                    "OPERATION_DEADLINE_EXCEEDED",
                    now,
                )
                op.fencing_token += 1
                op.lease_expiry = None
                continue
            if op.status == "running" and op.lease_expiry and service.utc(op.lease_expiry) <= now:
                op.fencing_token += 1
                if op.attempts >= 2:
                    op.status, op.error_code, op.finished_at = "failed", "WORKER_LOST", now
                    op.lease_expiry = None
                    continue
                op.status, op.dispatched_at, op.lease_expiry = "queued", None, None
            if op.status == "queued" and (
                op.dispatched_at is None
                or (now - service.utc(op.dispatched_at)).total_seconds() > 10
            ):
                # Durable operation row doubles as outbox. Re-dispatch after a crash is safe
                # because claiming is fenced; Redis is never the task fact source.
                pending.append((str(op.id), op.fencing_token))
    pool = ctx.get("redis")
    owned = pool is None
    try:
        if owned:
            from arq.connections import RedisSettings

            pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
        if pool is None:
            raise OSError("Redis unavailable")
        for op_id, fence in pending:
            await pool.enqueue_job(
                "evidence_search",
                op_id,
                _job_id=f"evidence:{op_id}:{fence}:{int(now.timestamp()) // 10}",
            )
            async with sessions() as db, db.begin():
                current = await db.get(EvidenceOperation, uuid.UUID(op_id))
                if current and current.status == "queued" and current.fencing_token == fence:
                    current.dispatched_at = now
    except (RedisError, OSError):
        logger.warning("evidence_dispatch_unavailable")
    finally:
        if owned and pool is not None:
            await pool.aclose()

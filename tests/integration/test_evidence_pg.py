from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.errors import ConflictError
from app.models.agent_run import AgentTrialRun
from app.models.evidence import EvidenceContext
from app.models.index_snapshot import IndexSnapshot, SystemState
from app.schemas.evidence import EvidenceSearch
from app.services import evidence as service
from tests.integration.test_migration import (  # noqa: F401
    _alembic,
    _apply_migration,
    isolated_db_url,
)
from tests.unit.test_agent_trial_store import setup_trial

pytestmark = pytest.mark.integration


def test_evidence_migration_retention_scope_and_deletion(request, tmp_path, monkeypatch):
    url = request.getfixturevalue("isolated_db_url")
    _apply_migration(url)

    async def verify():
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.connect() as conn:
                tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
                assert "evidence_contexts" in tables
                assert "evidence_operations" in tables
            async with sessions() as db:
                _, tools, _, docs = await setup_trial(db, tmp_path)
                op = await service.create_search(db, EvidenceSearch(query="testing"), "pg")
                context_id, op_id = op.context_id, op.id
                item = await db.get(EvidenceContext, context_id)
                snapshot_id = item.snapshot_id
                doc_id = docs[0].id
                await db.commit()
                from app.workers import evidence as worker

                monkeypatch.setattr(worker, "get_sessionmaker", lambda: sessions)
                monkeypatch.setattr(worker, "get_embedding_provider", lambda: tools.embedding)
                await asyncio.gather(
                    worker.evidence_search({}, str(op_id)), worker.evidence_search({}, str(op_id))
                )
                await db.refresh(op)
                assert op.status == "succeeded"
                assert op.attempts == 1
                # Remove unrelated retainers and prove the new context FK alone blocks GC.
                state = await db.get(SystemState, 1)
                state.active_index_snapshot_id = None
                await db.execute(delete(AgentTrialRun))
                await db.commit()
                with pytest.raises(IntegrityError):
                    await db.execute(delete(IndexSnapshot).where(IndexSnapshot.id == snapshot_id))
                await db.rollback()
                from app.core.config import get_settings
                from app.services.arq_enqueuer import FakeEnqueuer
                from app.services.document import DocumentService

                job = await DocumentService(get_settings(), FakeEnqueuer()).request_delete(
                    db, doc_id
                )
                assert job.kind == "delete_cleanup"
                await db.commit()
                with pytest.raises(ConflictError) as error:
                    await service.get_operation(db, op_id)
                assert error.value.code == "SOURCE_INVALIDATED"
                await db.rollback()
                await db.execute(delete(IndexSnapshot).where(IndexSnapshot.id == snapshot_id))
                await db.commit()
                item = await db.get(EvidenceContext, context_id)
                assert item.snapshot_id is None
        finally:
            await engine.dispose()

    asyncio.run(verify())
    _alembic(url, "downgrade", "0005_agent_runtime")
    _alembic(url, "upgrade", "head")

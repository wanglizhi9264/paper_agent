from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.contracts import AgentError, RuntimeState
from app.models.agent import AgentCheckpoint, AgentEvent
from app.models.session import Session
from app.services.agent_runs import RunStore, claim, reconcile
from app.services.agent_trial import TrialStore, create_trial
from tests.integration.test_migration import (
    _alembic,
    _apply_migration,
    isolated_db_url,  # noqa: F401 - pytest fixture registration
)
from tests.unit.test_agent_runs import setup as setup_full_run
from tests.unit.test_agent_trial_store import setup_trial

pytestmark = pytest.mark.integration


def test_full_runtime_pg_fencing_recovery_and_atomic_finalization(request, tmp_path):
    database_url = request.getfixturevalue("isolated_db_url")
    _apply_migration(database_url)

    async def verify():
        engine = create_async_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with sessions() as db:
                run, _, _, _, _, tools = await setup_full_run(db, tmp_path)
                claimed = await claim(db, run.id, "worker-a")
                await db.commit()
                token = claimed[1]
                store = RunStore(sessions, run.id, token, tools)
                await store.save(RuntimeState(llm_calls=2, tokens=500), "model.started")
                await db.refresh(run)
                original_deadline = run.deadline
                run.lease_expiry = datetime.now(UTC) - timedelta(seconds=1)
                await db.commit()
                await reconcile(db)
                await db.commit()
                await db.refresh(run)
                assert run.status == "queued"
                assert run.deadline == original_deadline
                assert run.state["tokens"] == 500
                with pytest.raises(AgentError, match="LEASE_LOST"):
                    await store.save(RuntimeState(), "model.started")
                claimed = await claim(db, run.id, "worker-b")
                await db.commit()
                new_store = RunStore(sessions, run.id, claimed[1], tools)
                state = RuntimeState(
                    phase="done",
                    llm_calls=2,
                    tokens=500,
                    answer="Insufficient evidence in the provided sources.",
                )
                await new_store.save(state, "answer.completed")
                await db.refresh(run)
                assert run.status == "succeeded"
                assert run.final_message_id is not None
                with pytest.raises(AgentError, match="LEASE_LOST"):
                    await new_store.save(state, "answer.completed")
                events = list(
                    (
                        await db.execute(select(AgentEvent).where(AgentEvent.run_id == run.id))
                    ).scalars()
                )
                assert sum(e.type == "run.completed" for e in events) == 1
                assert (
                    len(
                        list(
                            (
                                await db.execute(
                                    select(AgentCheckpoint).where(AgentCheckpoint.run_id == run.id)
                                )
                            ).scalars()
                        )
                    )
                    == 2
                )
        finally:
            await engine.dispose()

    asyncio.run(verify())


def test_agent_trial_pg_migration_transactions_and_lock(request, tmp_path):
    database_url = request.getfixturevalue("isolated_db_url")
    _apply_migration(database_url)

    async def verify():
        engine = create_async_engine(database_url)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.connect() as connection:
                indexes = await connection.run_sync(
                    lambda c: inspect(c).get_indexes("agent_trial_runs")
                )
                assert any(
                    i["name"] == "uq_agent_trial_active_session" and i["unique"] for i in indexes
                )
            async with sessions() as db:
                run, tools, _, _ = await setup_trial(db, tmp_path)
            store = TrialStore(sessions, run.id, tools)
            await store.save(RuntimeState(llm_calls=2, tokens=900), "model.started")
            await store.mark_error("INTERRUPTED", interrupted=True)
            async with sessions() as db:
                session = await db.get(Session, run.session_id)
                with pytest.raises(IntegrityError):
                    await create_trial(db, session, "Duplicate", run.pins, {})
                await db.rollback()
            # Finished message and journal commit exactly once under replay.
            await store.save(
                RuntimeState(phase="done", answer="No evidence", llm_calls=2, tokens=900),
                "answer.completed",
            )
            with pytest.raises(AgentError, match="RUN_TERMINAL"):
                await store.save(RuntimeState(phase="done"), "answer.completed")
            async with engine.connect() as a, engine.connect() as b:
                assert await a.scalar(text("SELECT pg_try_advisory_lock(731946218004)"))
                assert not await b.scalar(text("SELECT pg_try_advisory_lock(731946218004)"))
                await a.execute(text("SELECT pg_advisory_unlock(731946218004)"))
                assert await b.scalar(text("SELECT pg_try_advisory_lock(731946218004)"))
                await b.execute(text("SELECT pg_advisory_unlock(731946218004)"))
        finally:
            await engine.dispose()

    asyncio.run(verify())
    _alembic(database_url, "downgrade", "0003_failed_at_tz")
    _apply_migration(database_url)

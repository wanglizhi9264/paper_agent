from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agent.contracts import AgentError, RuntimeState
from app.agent.graph import run_graph
from app.agent.runtime import PaperAgent
from app.agent.skills import SkillRegistry
from app.api.errors import ConflictError, DependencyUnavailableError
from app.core.config import get_settings
from app.models.agent import AgentCheckpoint, AgentEvent, AgentRun, AgentRunOutbox
from app.models.session import Message, Session
from app.schemas.agent import AgentRunCreate
from app.services.agent_runs import RunStore, cancel_run, claim, create_run, reconcile, resume_run
from app.services.agent_trial import CorpusTools
from tests.unit.test_paper_agent import Backend, MemoryStore, QuarterCounter, ScriptedLLM, evidence
from tests.unit.test_retrieval_evidence_service import corpus


async def setup(db, tmp_path):
    docs, embedding = await corpus(db, tmp_path)
    session = Session(
        title="Run test", scope_type="documents", scope_payload={"document_ids": [str(docs[0].id)]}
    )
    db.add(session)
    await db.flush()
    tokenizer = tmp_path / "tokenizer"
    tokenizer.mkdir(exist_ok=True)
    (tokenizer / "agent_manifest.json").write_text("{}")
    settings = get_settings().model_copy(
        update={"agent_runtime_enabled": True, "agent_tokenizer_path": tokenizer}
    )
    catalog = SkillRegistry(Path("agent_skills"))
    body = AgentRunCreate(
        session_id=session.id, query="Explain the method", skill_names=["method-analysis"]
    )
    run = await create_run(db, body, str(uuid.uuid4()), settings, catalog)
    await db.commit()
    sessions = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    tools = CorpusTools(sessions, run.pins, embedding, settings, QuarterCounter(), pinned=True)
    return run, body, settings, catalog, sessions, tools


async def test_run_idempotency_scope_and_transaction(async_sqlite_session, tmp_path):
    db = async_sqlite_session
    run, body, settings, catalog, sessions, tools = await setup(db, tmp_path)
    same = await create_run(db, body, run.idempotency_key, settings, catalog)
    assert same.id == run.id
    with pytest.raises(ConflictError) as conflict:
        await create_run(
            db,
            body.model_copy(update={"query": "Different"}),
            run.idempotency_key,
            settings,
            catalog,
        )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(ConflictError) as conflict:
        await create_run(db, body, str(uuid.uuid4()), settings, catalog)
    assert conflict.value.code == "SESSION_RUN_ACTIVE"
    with pytest.raises(DependencyUnavailableError) as disabled:
        await create_run(
            db,
            body,
            "disabled",
            settings.model_copy(update={"agent_runtime_enabled": False}),
            catalog,
        )
    assert disabled.value.code == "AGENT_DISABLED"
    assert (
        len(
            list(
                (
                    await db.execute(select(Message).where(Message.session_id == run.session_id))
                ).scalars()
            )
        )
        == 1
    )
    assert (
        len(
            list(
                (
                    await db.execute(select(AgentRunOutbox).where(AgentRunOutbox.run_id == run.id))
                ).scalars()
            )
        )
        == 1
    )


async def test_lease_fencing_and_atomic_completion(async_sqlite_session, tmp_path):
    db = async_sqlite_session
    run, _, _, _, sessions, tools = await setup(db, tmp_path)
    claimed = await claim(db, run.id, "worker-a")
    await db.commit()
    assert claimed
    store = RunStore(sessions, run.id, claimed[1], tools)
    state = RuntimeState(llm_calls=2, tokens=700)
    await store.save(state, "model.started")
    async with sessions() as other, other.begin():
        stored = await other.get(AgentRun, run.id)
        stored.fencing_token += 1
    with pytest.raises(AgentError, match="LEASE_LOST"):
        await store.save(state, "model.started")
    new_store = RunStore(sessions, run.id, claimed[1] + 1, tools)
    state.phase = "done"
    state.answer = "Insufficient evidence in the provided sources."
    await new_store.save(state, "answer.completed")
    async with sessions() as other:
        stored = await other.get(AgentRun, run.id)
        assert stored.status == "succeeded"
        assert stored.final_message_id
        assert stored.snapshot_id is None
        events = list(
            (await other.execute(select(AgentEvent).where(AgentEvent.run_id == run.id))).scalars()
        )
        assert sum(e.type == "run.completed" for e in events) == 1
        assert (
            len(
                list(
                    (
                        await other.execute(
                            select(AgentCheckpoint).where(AgentCheckpoint.run_id == run.id)
                        )
                    ).scalars()
                )
            )
            == 2
        )
        assert (
            len(
                list(
                    (await other.execute(select(Message).where(Message.run_id == run.id))).scalars()
                )
            )
            == 1
        )


async def test_cancel_and_recovery_keep_budget(async_sqlite_session, tmp_path):
    db = async_sqlite_session
    run, _, settings, catalog, sessions, tools = await setup(db, tmp_path)
    claimed = await claim(db, run.id, "old-worker")
    run.lease_expiry = datetime.now(UTC) - timedelta(seconds=1)
    run.state = RuntimeState(llm_calls=3, tokens=1000).model_dump(mode="json")
    await db.commit()
    await reconcile(db)
    await db.commit()
    await db.refresh(run)
    assert run.status == "queued"
    assert run.recovery_count == 1
    assert run.state["tokens"] == 1000
    assert run.fencing_token > claimed[1]
    cancelled = await cancel_run(db, run.id)
    assert cancelled.status == "cancelled"
    await db.commit()
    assert await claim(db, run.id, "new-worker") is None


async def test_langgraph_uses_committed_state(registry):
    state = RuntimeState(phase="finalize", evidence=[evidence()])
    provider = ScriptedLLM(["Supported [1]."])
    store = MemoryStore()
    result = await run_graph(
        PaperAgent(provider, QuarterCounter(), Backend(), store, registry),
        state,
        "Question",
        [],
        datetime.now(UTC) + timedelta(seconds=10),
    )
    assert result.phase == "done"
    assert result.citations
    assert store.events[-1] == "answer.completed"


@pytest.fixture
def registry():
    return SkillRegistry(Path("agent_skills"))


async def test_graceful_shutdown_exposes_resume_without_resetting_budget(
    async_sqlite_session, tmp_path
):
    db = async_sqlite_session
    run, _, settings, catalog, sessions, tools = await setup(db, tmp_path)
    claimed = await claim(db, run.id, "worker")
    await db.commit()
    deadline = run.deadline
    store = RunStore(sessions, run.id, claimed[1], tools)
    await store.save(RuntimeState(llm_calls=2, tokens=500), "model.started")
    await store.interrupt()
    await db.refresh(run)
    assert run.status == "interrupted"
    resumed = await resume_run(db, run.id, "resume-once", settings, catalog)
    assert resumed.status == "queued"
    assert resumed.deadline == deadline.replace(tzinfo=None)
    assert resumed.state["tokens"] == 500
    assert resumed.error_code is None


async def test_resume_rejects_manifest_drift(async_sqlite_session, tmp_path):
    db = async_sqlite_session
    run, _, settings, catalog, _, _ = await setup(db, tmp_path)
    run.status = "interrupted"
    await db.commit()
    with pytest.raises(ConflictError) as conflict:
        await resume_run(
            db, run.id, "resume", settings.model_copy(update={"llm_model": "different"}), catalog
        )
    assert conflict.value.code == "RESUME_INCOMPATIBLE"

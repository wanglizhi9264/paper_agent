from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agent.contracts import Action, AgentError, RuntimeState
from app.core.config import get_settings
from app.models.agent_run import AgentTrialRun
from app.models.enums import SessionScopeType
from app.models.session import Message, Session
from app.schemas.search import SearchScope
from app.services.agent_trial import CorpusTools, TrialStore, corpus_pins, create_trial
from tests.unit.test_paper_agent import QuarterCounter
from tests.unit.test_retrieval_evidence_service import corpus


async def setup_trial(db, tmp_path):
    docs, adapter = await corpus(db, tmp_path)
    session = Session(
        title="Agent test",
        scope_type=SessionScopeType.DOCUMENTS,
        scope_payload={"document_ids": [str(docs[0].id)]},
    )
    db.add(session)
    await db.flush()
    pins = await corpus_pins(db, SearchScope(type="documents", document_ids=[docs[0].id]))
    run = await create_trial(db, session, "Question", pins, {})
    await db.commit()
    sessions = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    tools = CorpusTools(sessions, pins, adapter, get_settings(), QuarterCounter())
    return run, tools, sessions, docs


async def test_scope_and_source_version_checked(async_sqlite_session, tmp_path):
    run, tools, sessions, docs = await setup_trial(async_sqlite_session, tmp_path)
    listing = await tools.execute(Action(tool="list_papers"))
    assert [d["document_id"] for d in listing.data] == [str(docs[0].id)]
    with pytest.raises(AgentError, match="SCOPE_VIOLATION"):
        await tools.execute(
            Action(
                tool="search_papers",
                arguments={"query": "testing", "document_ids": [str(docs[1].id)]},
            )
        )
    with pytest.raises(AgentError, match="SCOPE_VIOLATION"):
        await tools.execute(
            Action(tool="read_chunks", arguments={"chunk_ids": [str(uuid.uuid4())]})
        )
    response = await tools.execute(Action(tool="search_papers", arguments={"query": "testing"}))
    assert response.evidence
    assert {e.source.document_id for e in response.evidence} == {docs[0].id}
    docs[0].status = "deleting"
    await async_sqlite_session.commit()
    with pytest.raises(AgentError, match="SOURCE_INVALIDATED"):
        await tools.execute(Action(tool="list_papers"))


async def test_checkpoint_and_final_message_atomic_no_duplicates(async_sqlite_session, tmp_path):
    run, tools, sessions, _ = await setup_trial(async_sqlite_session, tmp_path)
    store = TrialStore(sessions, run.id, tools)
    state = RuntimeState(llm_calls=1, tokens=100)
    await store.save(state, "model.started")
    async with sessions() as db:
        saved = await db.get(AgentTrialRun, run.id)
        assert saved.state["tokens"] == 100
        assert len(saved.events) == 1
    state.phase = "done"
    state.answer = "Insufficient evidence in the provided sources."
    await store.save(state, "answer.completed")
    with pytest.raises(AgentError, match="RUN_TERMINAL"):
        await store.save(state, "answer.completed")
    async with sessions() as db:
        saved = await db.get(AgentTrialRun, run.id)
        assert saved.status == "succeeded"
        assert saved.final_message_id is not None
        messages = list(
            (
                await db.execute(select(Message).where(Message.session_id == run.session_id))
            ).scalars()
        )
        assert len(messages) == 2


async def test_session_run_unique_constraint(async_sqlite_session, tmp_path):
    run, _, _, _ = await setup_trial(async_sqlite_session, tmp_path)
    session = await async_sqlite_session.get(Session, run.session_id)
    with pytest.raises(IntegrityError):
        await create_trial(async_sqlite_session, session, "Duplicate", run.pins, {})
    await async_sqlite_session.rollback()


async def test_interruption_preserves_original_budget_and_deadline(async_sqlite_session, tmp_path):
    run, tools, sessions, _ = await setup_trial(async_sqlite_session, tmp_path)
    store = TrialStore(sessions, run.id, tools)
    state = RuntimeState(llm_calls=3, tool_calls=2, tokens=900)
    await store.save(state, "tool.completed")
    await store.mark_error("INTERRUPTED", interrupted=True)
    async with sessions() as db:
        saved = await db.get(AgentTrialRun, run.id)
        assert saved.status == "interrupted"
        assert saved.state["tokens"] == 900
        assert saved.deadline == run.deadline.replace(tzinfo=None)

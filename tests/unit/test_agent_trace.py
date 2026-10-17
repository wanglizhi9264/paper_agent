from __future__ import annotations

import json
import math
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agent.contracts import Action, Observation, RuntimeState
from app.agent.runtime import PaperAgent
from app.agent.skills import SkillRegistry
from app.models.chunk import Chunk
from app.models.document import Document
from app.retrieval.bm25 import BM25Index
from app.schemas.trace import AgentTraceOut
from app.services.agent_runs import RunStore, claim, prune
from app.services.agent_trace import get_trace
from tests.unit.test_agent_runs import setup
from tests.unit.test_paper_agent import Backend, MemoryStore, QuarterCounter, ScriptedLLM, evidence


def test_bm25_explanation_matches_hand_calculation_and_actual_scores():
    index = BM25Index()
    index.build([(1, "mdm mdm fid"), (2, "fid"), (3, "diffusion")])
    explanation = index.explain("MDM mdm FID", [1, 2])
    assert explanation["tokens"] == ["mdm", "mdm", "fid"]
    assert explanation["n_docs"] == 3
    assert explanation["avgdl"] == pytest.approx(5 / 3)
    expected_mdm = math.log(1 + 2.5 / 1.5) * 2 * 2.5 / (2 + 1.5 * (0.25 + 0.75 * 3 / (5 / 3)))
    mdm = next(t for t in explanation["candidates"]["1"]["terms"] if t["term"] == "mdm")
    assert mdm["tf"] == 2
    assert mdm["contribution"] == pytest.approx(expected_mdm)
    for doc_id, score in index.search("MDM mdm FID"):
        assert sum(
            t["contribution"] for t in explanation["candidates"][str(doc_id)]["terms"]
        ) == pytest.approx(score)
    assert index.search("MDM mdm FID", scope_doc_ids={2}) == [
        next(row for row in index.search("MDM mdm FID") if row[0] == 2)
    ]
    long = index.explain(" ".join(f"term{i}" for i in range(200)), [1])
    assert long["terms_truncated"]
    assert len(long["candidates"]["1"]["terms"]) == 64


async def test_recorded_trace_uses_actual_search_and_pinned_candidate_scope(
    async_sqlite_session, tmp_path
):
    db = async_sqlite_session
    run, _, settings, _, sessions, tools = await setup(db, tmp_path)
    run.pins = {**run.pins, "trace_enabled": True}
    tools.pins = run.pins
    claimed = await claim(db, run.id, "trace-worker")
    await db.commit()
    assert claimed
    store = RunStore(sessions, run.id, claimed[1], tools)
    action = Action(tool="search_papers", arguments={"query": "testing retrieval"}, call_id="s1")
    state = RuntimeState(action=action, tool_calls=1, search_calls=1)
    await store.save(state, "tool.started")
    observation = await tools.execute(action)
    assert observation.trace is not None
    assert "raw_content" not in json.dumps(observation.trace)
    from app.services.agent_trial import CorpusTools

    without_trace = CorpusTools(
        sessions,
        {**run.pins, "trace_enabled": False},
        tools.embedding,
        settings,
        tools.counter,
        pinned=True,
    )
    plain = await without_trace.execute(action)
    assert plain.trace is None
    assert observation.model_dump(exclude={"trace"}) == plain.model_dump(exclude={"trace"})
    state.observations.append(observation)
    state.completed_call_id = action.call_id
    await store.save(state, "tool.completed")
    async with sessions() as other:
        trace = await get_trace(other, run.id, settings)
        assert trace.availability == "recorded"
        step = trace.steps[0]
        assert step.arguments == {"query": "testing retrieval"}
        assert step.retrieval == observation.trace
        assert step.duration_ms is not None
        assert step.duration_ms >= 0
        assert step.candidates
        assert all(str(c.document_id) in run.pins["documents"] for c in step.candidates)
        assert all(len(c.raw_content) <= 1200 for c in step.candidates)
        assert step.unavailable_candidates == 0
        debug = step.retrieval
        assert debug["embedding_manifest"]["dimension"] == 32
        assert debug["index"]["scoped_vectors"] == 3
        lists = debug["fusion_lists"]
        for fid, score, _ in debug["rrf"]:
            expected = sum(
                1 / (debug["rrf_k"] + rank)
                for entries in lists.values()
                for rank, row in enumerate(entries, 1)
                if row[0] == fid
            )
            assert score == pytest.approx(expected)
        # An unrelated scoped-out chunk cannot be exposed, even if a stored mapping is damaged.
        outsider = await other.scalar(
            select(Chunk).where(
                Chunk.document_id.not_in([uuid.UUID(k) for k in run.pins["documents"]])
            )
        )
        assert outsider is not None
        from app.models.agent import AgentStep

        stored_step = await other.scalar(select(AgentStep).where(AgentStep.run_id == run.id))
        result = dict(stored_step.result_json)
        result["trace"] = {**result["trace"], "candidate_chunk_ids": {"999": str(outsider.id)}}
        stored_step.result_json = result
        await other.commit()
        damaged = await get_trace(other, run.id, settings)
        assert damaged.steps[0].candidates == []
        assert damaged.steps[0].unavailable_candidates == 1
        document = await other.get(Document, uuid.UUID(next(iter(run.pins["documents"]))))
        document.status = "deleting"
        stored_step.result_json = observation.model_dump(mode="json")
        await other.commit()
        invalidated = await get_trace(other, run.id, settings)
        assert invalidated.steps[0].candidates == []
        assert invalidated.steps[0].unavailable_candidates > 0


async def test_trace_unrecorded_error_and_expiry_are_not_fabricated(async_sqlite_session, tmp_path):
    db = async_sqlite_session
    run, _, settings, _, sessions, tools = await setup(db, tmp_path)
    claimed = await claim(db, run.id, "trace-worker")
    await db.commit()
    assert claimed
    store = RunStore(sessions, run.id, claimed[1], tools)
    state = RuntimeState(
        action=Action(tool="search_papers", arguments={"query": "MDM FID"}, call_id="s1")
    )
    await store.save(state, "tool.started")
    state.observations.append(
        Observation(tool="search_papers", status="error", error_code="TOOL_TIMEOUT")
    )
    state.completed_call_id = "s1"
    await store.save(state, "tool.completed")
    async with sessions() as other:
        trace = await get_trace(other, run.id, settings)
        assert trace.availability == "not_recorded"
        assert trace.steps[0].arguments is None
        assert trace.steps[0].error_code == "TOOL_TIMEOUT"
        assert trace.steps[0].retrieval is None
        assert trace.steps[0].candidates == []
        stored = await other.get(type(run), run.id)
        stored.finished_at = datetime.now(UTC) - timedelta(days=settings.agent_retention_days + 1)
        stored.status = "failed"
        state.context_trace = {"input_tokens": 100}
        state.observations[0].trace = {"retained_only_before_expiry": True}
        stored.state = state.model_dump(mode="json")
        await other.commit()
        expired = await get_trace(other, run.id, settings)
        assert expired.availability == "expired"
        assert expired.steps == expired.events == []
        await prune(other, settings.agent_retention_days)
        await other.commit()
        retained = RuntimeState.model_validate(stored.state)
        assert retained.context_trace == {}
        assert retained.observations[0].trace is None


@pytest.mark.parametrize("fusion", ["four_way_rrf", "retriever_best_rank_rrf"])
async def test_dual_query_trace_explains_actual_fusion_lists(
    async_sqlite_session, tmp_path, fusion
):
    from app.core.config import get_settings
    from app.schemas.search import SearchRequest, SearchScope
    from app.services.retrieval import search_corpus
    from app.services.retrieval_queries import RetrievalQueries
    from tests.unit.test_retrieval_evidence_service import corpus

    docs, embedding = await corpus(async_sqlite_session, tmp_path)
    result = await search_corpus(
        async_sqlite_session,
        SearchRequest(
            query="testing retrieval",
            scope=SearchScope(type="documents", document_ids=[docs[0].id]),
            debug=True,
        ),
        embedding,
        settings=get_settings().model_copy(update={"retrieval_cross_language_fusion": fusion}),
        retrieval_queries=RetrievalQueries(
            original="testing retrieval", english="retrieval", mode="cjk_to_english_dual"
        ),
    )
    debug = result.debug
    assert debug is not None
    assert len(debug["fusion_lists"]) == (4 if fusion == "four_way_rrf" else 2)
    for fid, score, _ in debug["rrf"]:
        assert score == pytest.approx(
            sum(
                1 / (60 + rank)
                for entries in debug["fusion_lists"].values()
                for rank, row in enumerate(entries, 1)
                if row[0] == fid
            )
        )


async def test_native_tool_messages_exclude_retrieval_trace():
    from tests.unit.test_agent_native_stream import NativeProvider

    class TraceBackend(Backend):
        async def execute(self, action):
            result = await super().execute(action)
            result.trace = {"native_trace_sentinel": "x"}
            return result

    provider = NativeProvider()
    agent = PaperAgent(
        provider,
        QuarterCounter(),
        TraceBackend(evidence()),
        MemoryStore(),
        SkillRegistry(Path("agent_skills")),
        action_protocol="native",
        trace_enabled=True,
    )
    result = await agent.run(
        RuntimeState(), "Question", [], datetime.now(UTC) + timedelta(seconds=30)
    )
    assert result.observations[0].trace
    assert all(
        "native_trace_sentinel" not in message.content
        for request in provider.requests
        for message in request
    )


async def test_trace_stays_out_of_model_context_and_records_packing():
    state = RuntimeState(
        phase="finalize",
        evidence=[evidence()],
        observations=[
            Observation(tool="search_papers", status="ok", trace={"secret_trace_sentinel": "x"})
        ],
    )
    store = MemoryStore()
    agent = PaperAgent(
        ScriptedLLM(["Supported [1]."]),
        QuarterCounter(),
        Backend(),
        store,
        SkillRegistry(Path("agent_skills")),
        trace_enabled=True,
    )
    assert "secret_trace_sentinel" not in agent._instructions(state)
    result = await agent.run(state, "Question", [], datetime.now(UTC) + timedelta(seconds=60))
    assert result.context_trace["input_tokens"] > 0
    assert result.context_trace["packed_source_ids"] == [str(result.packed_sources[0].chunk_id)]
    assert "context.prepared" in store.events
    assert "model.completed" in store.events


def test_trace_frontend_schema_and_openapi_contract(client):
    schema = client.get("/api/openapi.json").json()
    assert "/api/v1/agent/runs/{run_id}/trace" in schema["paths"]
    assert (
        schema["components"]["schemas"]["AgentRunCreate"]["properties"]["trace_enabled"]["default"]
        is False
    )
    saved = json.loads(Path("frontend/src/types/trace-contract.json").read_text(encoding="utf-8"))
    assert saved == AgentTraceOut.model_json_schema()

from __future__ import annotations

import uuid

import httpx

from app.api import agent as agent_api
from app.db.session import get_session
from app.main import create_app
from tests.unit.test_agent_runs import setup
from tests.unit.test_paper_agent import QuarterCounter


async def test_http_contract_idempotency_cancel_and_sse(
    async_sqlite_session, tmp_path, monkeypatch
):
    db = async_sqlite_session
    seeded, body, settings, catalog, sessions, _ = await setup(db, tmp_path)
    monkeypatch.setattr(agent_api, "get_settings", lambda: settings)
    monkeypatch.setattr(agent_api, "get_sessionmaker", lambda: sessions)

    async def no_dispatch():
        return None

    monkeypatch.setattr(agent_api, "dispatch", no_dispatch)

    async def db_dependency():
        async with sessions() as session:
            yield session
            await session.commit()

    app = create_app()
    app.dependency_overrides[get_session] = db_dependency
    app.dependency_overrides[agent_api.registry] = lambda: catalog
    app.dependency_overrides[agent_api.token_counter] = lambda: QuarterCounter()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        payload = body.model_dump(mode="json")
        key = seeded.idempotency_key
        replay = await client.post(
            "/api/v1/agent/runs", json=payload, headers={"Idempotency-Key": key}
        )
        assert replay.status_code == 202
        assert replay.json()["run_id"] == str(seeded.id)
        assert "query" not in replay.json()
        assert "pins" not in replay.json()
        trace = await client.get(f"/api/v1/agent/runs/{seeded.id}/trace")
        assert trace.status_code == 200
        assert trace.json()["availability"] == "not_recorded"
        assert trace.json()["query"] == body.query
        assert (await client.get(f"/api/v1/agent/runs/{uuid.uuid4()}/trace")).status_code == 404
        missing_key = await client.post("/api/v1/agent/runs", json=payload)
        assert missing_key.status_code == 422
        cancel = await client.post(f"/api/v1/agent/runs/{seeded.id}/cancel")
        assert cancel.json()["status"] == "cancelled"
        events = await client.get(f"/api/v1/agent/runs/{seeded.id}/events")
        assert events.status_code == 200
        assert events.text.count("event: run.cancelled") == 1
        resumed = await client.get(
            f"/api/v1/agent/runs/{seeded.id}/events", headers={"Last-Event-ID": "1"}
        )
        assert "event: run.created" not in resumed.text
        invalid = await client.get(
            f"/api/v1/agent/runs/{seeded.id}/events", headers={"Last-Event-ID": "99999"}
        )
        assert invalid.status_code == 409
        unknown = await client.get(f"/api/v1/agent/runs/{uuid.uuid4()}")
        assert unknown.status_code == 404


def test_openapi_agent_request_forbids_scope_override(client):
    schema = client.get("/api/openapi.json").json()
    request = schema["components"]["schemas"]["AgentRunCreate"]
    assert request["additionalProperties"] is False
    assert set(request["required"]) == {"session_id", "query"}
    assert "/api/v1/agent/runs/{run_id}/events" in schema["paths"]


def test_frontend_agent_contract_matches_pydantic():
    import json
    from pathlib import Path

    from app.schemas.agent import AgentRunOut

    saved = json.loads(Path("frontend/src/types/agent-contract.json").read_text(encoding="utf-8"))
    assert saved == AgentRunOut.model_json_schema()

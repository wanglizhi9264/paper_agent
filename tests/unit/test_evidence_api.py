from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from app.api import evidence as api
from app.core.config import get_settings
from app.db.session import get_session
from app.main import create_app
from app.models.evidence import EvidenceContext
from app.schemas.evidence import EvidenceSearch
from app.services import evidence as service
from tests.unit.test_agent_trial_store import setup_trial


async def test_evidence_api_schema_idempotency_and_operation_poll(
    async_sqlite_session, tmp_path, monkeypatch
):
    db = async_sqlite_session
    _, _, _, docs = await setup_trial(db, tmp_path)
    config = get_settings().model_copy(update={"mcp_enabled": True})
    monkeypatch.setattr(api, "get_settings", lambda: config)

    async def dispatch(_ctx):
        return None

    monkeypatch.setattr(api, "dispatch_evidence", dispatch)

    async def override():
        yield db

    app = create_app()
    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        papers = await client.post(
            "/api/v1/evidence/papers",
            json={"scope": {"type": "documents", "document_ids": [str(docs[0].id)]}},
        )
        assert papers.status_code == 200
        assert [p["document_id"] for p in papers.json()["items"]] == [str(docs[0].id)]
        missing = await client.post("/api/v1/evidence/search", json={"query": "x"})
        assert missing.status_code == 422
        response = await client.post(
            "/api/v1/evidence/search",
            json={"query": "testing"},
            headers={"Idempotency-Key": "api-key"},
        )
        assert response.status_code == 202
        op_id = response.json()["operation_id"]
        repeated = await client.post(
            "/api/v1/evidence/search",
            json={"query": "testing"},
            headers={"Idempotency-Key": "api-key"},
        )
        assert repeated.json()["operation_id"] == op_id
        polled = await client.get(f"/api/v1/evidence/operations/{op_id}")
        assert polled.json()["status"] == "queued"
        cancelled = await client.post(f"/api/v1/evidence/operations/{op_id}/cancel")
        assert cancelled.json()["status"] == "cancelled"
        schema = (await client.get("/api/openapi.json")).json()
        assert schema["paths"]["/api/v1/evidence/chunks"]["post"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["$ref"].endswith("EvidenceResponse")


def test_mcp_feature_is_opt_in(client):
    response = client.post("/api/v1/evidence/papers", json={})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MCP_DISABLED"


async def test_cancel_endpoint_cannot_replay_expired_success_payload(
    async_sqlite_session, tmp_path, monkeypatch
):
    db = async_sqlite_session
    await setup_trial(db, tmp_path)
    op = await service.create_search(db, EvidenceSearch(query="test"), "expired-success")
    item = await db.get(EvidenceContext, op.context_id)
    op.status = "succeeded"
    op.finished_at = datetime.now(UTC)
    op.result = service.response(item, []).model_dump(mode="json")
    item.created_at = datetime.now(UTC) - timedelta(hours=2)
    item.expires_at = datetime.now(UTC) - timedelta(hours=1)
    await db.commit()
    monkeypatch.setattr(
        api, "get_settings", lambda: get_settings().model_copy(update={"mcp_enabled": True})
    )

    async def override():
        yield db

    app = create_app()
    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        cancelled = await client.post(f"/api/v1/evidence/operations/{op.id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["result"] is None
        result = await client.get(f"/api/v1/evidence/operations/{op.id}")
        assert result.status_code == 409
        assert result.json()["error"]["code"] == "CONTEXT_EXPIRED"

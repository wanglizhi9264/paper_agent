from __future__ import annotations

from app.api import health


def test_live_endpoint(client) -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_reports_503_when_postgres_down(client, monkeypatch) -> None:
    async def _down() -> health.ComponentHealth:
        return health.ComponentHealth(status="down", detail="ConnectionRefused")

    monkeypatch.setattr(health, "_check_postgres", _down)

    async def _ok_redis() -> health.ComponentHealth:
        return health.ComponentHealth(status="ok")

    monkeypatch.setattr(health, "_check_redis", _ok_redis)
    monkeypatch.setattr(health, "_check_index_snapshot", _ok_redis)

    resp = client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "down"
    assert body["components"]["postgres"]["status"] == "down"
    assert body["components"]["index"]["status"] == "ok"


def test_ready_ok_when_all_components_ok(client, monkeypatch) -> None:
    async def _ok() -> health.ComponentHealth:
        return health.ComponentHealth(status="ok")

    monkeypatch.setattr(health, "_check_postgres", _ok)
    monkeypatch.setattr(health, "_check_redis", _ok)
    monkeypatch.setattr(health, "_check_index_snapshot", _ok)

    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

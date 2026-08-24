from __future__ import annotations

import importlib

from app.api.dependencies import parse_uuid


def test_request_id_header_echoed(client) -> None:
    resp = client.get("/health/live", headers={"x-request-id": "abc-123"})
    assert resp.headers["x-request-id"] == "abc-123"


def test_request_id_generated_when_missing(client) -> None:
    resp = client.get("/health/live")
    rid = resp.headers["x-request-id"]
    assert len(rid) == 36


def test_parse_uuid_valid() -> None:
    u = parse_uuid("550e8400-e29b-41d4-a716-446655440000")
    assert str(u) == "550e8400-e29b-41d4-a716-446655440000"


def test_parse_uuid_invalid_raises_valueerror() -> None:
    import pytest

    with pytest.raises(ValueError):
        parse_uuid("not-a-uuid")


def test_configure_logging_idempotent() -> None:
    from app.core import logging as logging_mod

    importlib.reload(logging_mod)
    logging_mod.configure_logging("DEBUG")
    logging_mod.configure_logging("INFO")

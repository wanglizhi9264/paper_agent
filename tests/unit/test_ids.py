from __future__ import annotations

from app.core.ids import new_uuid, new_uuid_str


def test_new_uuid_is_v4() -> None:
    u = new_uuid()
    assert u.version == 4


def test_new_uuid_str_format() -> None:
    s = new_uuid_str()
    assert len(s) == 36
    assert s.count("-") == 4

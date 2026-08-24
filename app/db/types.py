from __future__ import annotations

from typing import Any

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeEngine


def jsonb() -> TypeEngine[Any]:
    """A JSON type that uses native JSONB on PostgreSQL and generic JSON elsewhere.

    Production runs on PostgreSQL (JSONB). Unit tests that create tables on
    SQLite fall back to generic JSON so the ORM mapping can be exercised without
    a live Postgres instance.
    """
    return JSON().with_variant(JSONB(), "postgresql")

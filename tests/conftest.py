from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

# Provide a deterministic test environment BEFORE any app import (app.main
# builds the FastAPI instance at import time).
os.environ.setdefault("PAPER_RAG_ENV", "test")
os.environ.setdefault(
    "PAPER_RAG_DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test"
)
os.environ.setdefault("PAPER_RAG_REDIS_URL", "redis://127.0.0.1:6379/15")
os.environ.setdefault("PAPER_RAG_STORAGE_DIR", "./storage/_test")

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 - register models on metadata
from app.db.base import Base
from app.main import create_app


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="run integration tests (requires live PostgreSQL)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-integration"):
        return
    skip = pytest.mark.skip(reason="needs --run-integration and a live PostgreSQL")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    from app.core.config import reset_settings_cache

    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture()
async def async_sqlite_session() -> AsyncIterator[AsyncSession]:
    """In-memory async SQLite session with all ORM tables created.

    Used to unit-test services and the ingestion pipeline without a live
    PostgreSQL. JSONB falls back to generic JSON; PG-only CHECK constraints
    referencing functions are not enforced here.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session
    await engine.dispose()

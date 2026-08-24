from __future__ import annotations

import os
from collections.abc import Iterator

# Provide a deterministic test environment BEFORE any app import (app.main
# builds the FastAPI instance at import time).
os.environ.setdefault("PAPER_RAG_ENV", "test")
os.environ.setdefault(
    "PAPER_RAG_DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test"
)
os.environ.setdefault("PAPER_RAG_REDIS_URL", "redis://127.0.0.1:6379/15")
os.environ.setdefault("PAPER_RAG_STORAGE_DIR", "./storage/_test")

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


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

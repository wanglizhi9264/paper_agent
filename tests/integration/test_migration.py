"""Integration tests for the initial Alembic migration and DB constraints.

These require a live PostgreSQL reachable via PAPER_RAG_DATABASE_URL. Run with:

    PAPER_RAG_RUN_INTEGRATION=1 uv run pytest -m integration

They are skipped by default and never run in CI (CI has no Postgres).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid

import pytest
from sqlalchemy import MetaData, inspect
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture
def isolated_db_url() -> str:
    """Create a fresh throwaway database derived from PAPER_RAG_DATABASE_URL.

    The operator's configured URL must point at a Postgres where the connecting
    user can ``CREATE DATABASE``. Builds ``paper_rag_itest_<rand>`` on the same
    server, yields its URL, and drops it on teardown.
    """
    base = os.environ.get("PAPER_RAG_DATABASE_URL", "")
    assert base, "PAPER_RAG_DATABASE_URL must be set for integration tests"
    assert "/" in base, "cannot derive a temp db name from PAPER_RAG_DATABASE_URL"
    head, _orig_db = base.rsplit("/", 1)
    db_name = f"paper_rag_itest_{uuid.uuid4().hex[:8]}"
    iso_url = f"{head}/{db_name}"

    import asyncpg

    admin_url = base.replace("+asyncpg", "")
    server_part, _ = admin_url.rsplit("/", 1)

    async def _create() -> None:
        conn = await asyncpg.connect(server_part)
        try:
            await conn.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await conn.close()

    asyncio.run(_create())

    yield iso_url

    async def _drop() -> None:
        conn = await asyncpg.connect(server_part)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)')
        finally:
            await conn.close()

    asyncio.run(_drop())


def _apply_migration(url: str) -> None:
    env = {**os.environ, "PAPER_RAG_DATABASE_URL": url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr


def test_migration_applies_and_creates_all_tables(isolated_db_url: str) -> None:
    _apply_migration(isolated_db_url)

    expected_tables = {
        "documents",
        "document_versions",
        "chunks",
        "collections",
        "collection_documents",
        "ingestion_jobs",
        "index_snapshots",
        "system_state",
        "sessions",
        "messages",
        "retrieval_logs",
        "alembic_version",
    }

    async def _inspect() -> set[str]:
        engine = create_async_engine(isolated_db_url)
        try:
            async with engine.connect() as conn:
                names = await conn.run_sync(lambda c: set(inspect(c).get_table_names()))
            return names
        finally:
            await engine.dispose()

    names = asyncio.run(_inspect())
    assert expected_tables.issubset(names), f"missing: {expected_tables - names}"


def test_migration_check_constraints_enforced(isolated_db_url: str) -> None:
    _apply_migration(isolated_db_url)

    import asyncpg

    async def _violations() -> list[str]:
        conn = await asyncpg.connect(isolated_db_url.replace("+asyncpg", ""))
        try:
            for sql in (
                "INSERT INTO documents (id, filename, stored_filename, media_type, "
                "extension, sha256, file_size) VALUES "
                "(gen_random_uuid(),'a','b','application/pdf','exe','x',1)",
                "INSERT INTO documents (id, filename, stored_filename, media_type, "
                "extension, sha256, file_size) VALUES "
                "(gen_random_uuid(),'a','b','application/pdf','md','x',0)",
                "INSERT INTO ingestion_jobs (id, document_id, kind, progress) VALUES "
                "(gen_random_uuid(), gen_random_uuid(),'ingest', 200)",
            ):
                try:
                    await conn.execute(sql)
                    return ["constraint not enforced"]
                except asyncpg.CheckViolationError:
                    continue
            return []
        finally:
            await conn.close()

    assert asyncio.run(_violations()) == []


def test_migration_metadata_matches_orm(isolated_db_url: str) -> None:
    _apply_migration(isolated_db_url)

    import app.models  # noqa: F401
    from app.db.base import Base

    async def _reflect() -> MetaData:
        engine = create_async_engine(isolated_db_url)
        try:
            md = MetaData()
            async with engine.connect() as conn:
                await conn.run_sync(md.reflect)
            return md
        finally:
            await engine.dispose()

    reflected = asyncio.run(_reflect())
    orm_tables = set(Base.metadata.tables)
    reflected_tables = set(reflected.tables)
    assert orm_tables == reflected_tables, (
        f"only in ORM: {orm_tables - reflected_tables}; only in DB: {reflected_tables - orm_tables}"
    )

    chunks = reflected.tables["chunks"]
    cons_names = {c.name for c in chunks.constraints if c.name}
    assert "uq_chunk_version_index" in cons_names
    assert "ck_chunk_faiss_id_nonneg" in cons_names

    documents = reflected.tables["documents"]
    doc_cons = {c.name for c in documents.constraints if c.name}
    assert "ck_document_extension_allowlist" in doc_cons
    fk_names = {fk.name for fk in documents.foreign_keys if fk.name}
    assert "fk_document_active_version" in fk_names

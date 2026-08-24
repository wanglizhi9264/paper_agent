from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session as DBSession

import app.models  # noqa: F401
from app.db.base import Base
from app.models.chunk import Chunk, DocumentVersion
from app.models.collection import Collection, CollectionDocument
from app.models.document import Document
from app.models.enums import (
    ChunkKind,
    DocumentStatus,
    JobKind,
    JobStage,
    JobStatus,
    SessionScopeType,
)
from app.models.index_snapshot import IndexSnapshot, IndexSnapshotStatus, SystemState
from app.models.job import IngestionJob
from app.models.retrieval_log import RetrievalLog
from app.models.session import Message, MessageRole, MessageStatus, Session


@pytest.fixture
def sqlite_session() -> DBSession:
    # SQLite ignores PG-specific JSONB/UUID types (SQLAlchemy falls back to
    # generic types). CHECK constraints referencing PG functions are not created
    # here; this fixture only validates ORM mapping + defaults + relationships.
    engine = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _record):  # pragma: no cover - test infra
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with DBSession(engine) as session:
        yield session


def test_document_defaults(sqlite_session: DBSession) -> None:
    doc = Document(
        filename="paper.pdf",
        stored_filename="abc.pdf",
        media_type="application/pdf",
        extension="pdf",
        sha256="a" * 64,
        file_size=1024,
    )
    sqlite_session.add(doc)
    sqlite_session.commit()
    fetched = sqlite_session.get(Document, doc.id)
    assert fetched is not None
    assert fetched.status == DocumentStatus.UPLOADED
    assert fetched.chunk_count == 0
    assert fetched.created_at is not None


def test_document_version_and_chunk_relationship(sqlite_session: DBSession) -> None:
    doc = Document(
        filename="p.pdf",
        stored_filename="s.pdf",
        media_type="application/pdf",
        extension="pdf",
        sha256="b" * 64,
        file_size=10,
    )
    sqlite_session.add(doc)
    sqlite_session.flush()

    version = DocumentVersion(
        document_id=doc.id,
        chunk_config={"max_chunk_chars": 800},
    )
    sqlite_session.add(version)
    sqlite_session.flush()

    chunk = Chunk(
        document_id=doc.id,
        document_version_id=version.id,
        chunk_index=0,
        kind=ChunkKind.TEXT,
        section_path=["Intro"],
        raw_content="hello",
        retrieval_content="title\nIntro\nhello",
        content_hash="c" * 64,
        character_count=5,
    )
    sqlite_session.add(chunk)
    sqlite_session.commit()

    # Relationship: version.chunks
    refreshed = sqlite_session.get(DocumentVersion, version.id)
    assert refreshed is not None
    assert len(refreshed.chunks) == 1
    assert refreshed.chunks[0].raw_content == "hello"


def test_chunk_unique_version_index_enforced_in_metadata() -> None:
    chunks = Base.metadata.tables["chunks"]
    uq = [c for c in chunks.constraints if c.name == "uq_chunk_version_index"]
    assert len(uq) == 1


def test_collection_document_association(sqlite_session: DBSession) -> None:
    doc = Document(
        filename="p.md",
        stored_filename="s.md",
        media_type="text/markdown",
        extension="md",
        sha256="d" * 64,
        file_size=10,
    )
    coll = Collection(name="My Papers", description="test set")
    sqlite_session.add_all([doc, coll])
    sqlite_session.flush()
    assoc = CollectionDocument(collection_id=coll.id, document_id=doc.id)
    sqlite_session.add(assoc)
    sqlite_session.commit()

    found = sqlite_session.execute(
        select(CollectionDocument).where(
            CollectionDocument.collection_id == coll.id,
            CollectionDocument.document_id == doc.id,
        )
    ).scalar_one()
    assert found is not None


def test_ingestion_job_defaults(sqlite_session: DBSession) -> None:
    doc = Document(
        filename="p.pdf",
        stored_filename="s.pdf",
        media_type="application/pdf",
        extension="pdf",
        sha256="e" * 64,
        file_size=10,
    )
    sqlite_session.add(doc)
    sqlite_session.flush()
    job = IngestionJob(document_id=doc.id, kind=JobKind.INGEST)
    sqlite_session.add(job)
    sqlite_session.commit()
    fetched = sqlite_session.get(IngestionJob, job.id)
    assert fetched is not None
    assert fetched.status == JobStatus.QUEUED
    assert fetched.stage == JobStage.QUEUED
    assert fetched.progress == 0
    assert fetched.attempt == 1


def test_session_message_cascade(sqlite_session: DBSession) -> None:
    session = Session(
        title="Chat 1",
        scope_type=SessionScopeType.ALL,
        scope_payload={},
    )
    sqlite_session.add(session)
    sqlite_session.flush()
    msg = Message(
        session_id=session.id,
        role=MessageRole.USER,
        content="hi",
    )
    sqlite_session.add(msg)
    sqlite_session.commit()

    # Delete session cascades to message (DB-level ondelete CASCADE).
    msg_id = msg.id
    sqlite_session.delete(session)
    sqlite_session.commit()
    sqlite_session.expire_all()
    remaining = sqlite_session.execute(select(Message).where(Message.id == msg_id)).first()
    assert remaining is None


def test_index_snapshot_and_system_state(sqlite_session: DBSession) -> None:
    snap = IndexSnapshot()
    sqlite_session.add(snap)
    sqlite_session.flush()
    ss = sqlite_session.get(SystemState, 1)
    # Singleton seeded only by migration; ORM default inserts id=1 on new row.
    if ss is None:
        ss = SystemState(id=1, active_index_snapshot_id=snap.id)
        sqlite_session.add(ss)
        sqlite_session.commit()
    else:
        ss.active_index_snapshot_id = snap.id
        sqlite_session.commit()

    snap.status = IndexSnapshotStatus.ACTIVE
    snap.activated_at = datetime.now(UTC)
    sqlite_session.commit()
    fetched = sqlite_session.get(IndexSnapshot, snap.id)
    assert fetched is not None
    assert fetched.status == IndexSnapshotStatus.ACTIVE


def test_retrieval_log_optional_links(sqlite_session: DBSession) -> None:
    log = RetrievalLog(
        original_query="what is attention?",
        scope={"type": "all"},
        params_snapshot={"top_k": 8},
    )
    sqlite_session.add(log)
    sqlite_session.commit()
    fetched = sqlite_session.get(RetrievalLog, log.id)
    assert fetched is not None
    assert fetched.session_id is None
    assert fetched.message_id is None
    assert fetched.params_snapshot == {"top_k": 8}


def test_message_interrupted_status(sqlite_session: DBSession) -> None:
    session = Session(title="t", scope_type=SessionScopeType.ALL, scope_payload={})
    sqlite_session.add(session)
    sqlite_session.flush()
    msg = Message(
        session_id=session.id,
        role=MessageRole.ASSISTANT,
        status=MessageStatus.INTERRUPTED,
        content="partial answer...",
    )
    sqlite_session.add(msg)
    sqlite_session.commit()
    fetched = sqlite_session.get(Message, msg.id)
    assert fetched is not None
    assert fetched.status == MessageStatus.INTERRUPTED

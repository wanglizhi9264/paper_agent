from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.schemas.collection import CollectionCreate, CollectionOut, CollectionUpdate
from app.schemas.common import Page, to_rfc3339
from app.schemas.document import DocumentCreateResponse, DocumentOut
from app.schemas.job import JobOut


def test_document_create_response() -> None:
    did = uuid.uuid4()
    jid = uuid.uuid4()
    r = DocumentCreateResponse(document_id=did, job_id=jid, status="queued")
    dumped = r.model_dump(mode="json")
    assert dumped["document_id"] == str(did)
    assert dumped["status"] == "queued"


def test_document_out_serializes_datetimes_to_rfc3339() -> None:
    now = datetime.now(UTC)
    d = DocumentOut(
        id=uuid.uuid4(),
        filename="a.pdf",
        media_type="application/pdf",
        extension="pdf",
        title="A",
        sha256="x" * 64,
        file_size=10,
        status="ready",
        created_at=now,
        updated_at=now,
    )
    dumped = d.model_dump()
    assert dumped["created_at"].endswith("Z")
    assert "Z" in dumped["updated_at"]


def test_collection_create_validation() -> None:
    import pytest
    from pydantic import ValidationError

    CollectionCreate(name="My Set")  # ok
    with pytest.raises(ValidationError):
        CollectionCreate(name="")  # too short


def test_collection_out_dumps_with_z() -> None:
    now = datetime.now(UTC)
    c = CollectionOut(
        id=uuid.uuid4(), name="x", description="", document_count=0, created_at=now, updated_at=now
    )
    assert c.model_dump()["created_at"].endswith("Z")


def test_page_generic() -> None:
    p = Page[int](items=[1, 2], next_cursor="3", has_more=True)
    assert p.items == [1, 2]
    assert p.has_more is True


def test_job_out_serializes_optional_datetimes() -> None:
    now = datetime.now(UTC)
    j = JobOut(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        kind="ingest",
        status="queued",
        stage="queued",
        progress=0,
        attempt=1,
        started_at=None,
        finished_at=None,
        created_at=now,
        updated_at=now,
    )
    dumped = j.model_dump()
    assert dumped["started_at"] is None
    assert dumped["created_at"].endswith("Z")


def test_to_rfc3339_naive_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        to_rfc3339(datetime(2024, 1, 1))


def test_to_rfc3339_none() -> None:
    assert to_rfc3339(None) is None


def test_collection_update_optional_fields() -> None:
    u = CollectionUpdate()
    assert u.name is None
    assert u.description is None
    u2 = CollectionUpdate(name="New")
    assert u2.name == "New"

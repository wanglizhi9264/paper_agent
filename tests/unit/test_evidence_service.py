from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.errors import ConflictError, NotFoundError
from app.models.chunk import Chunk
from app.models.evidence import EvidenceContext, EvidenceOperation
from app.models.index_snapshot import IndexSnapshot
from app.schemas.evidence import ChunkRead, EvidenceChunks, EvidenceSearch, PaperListRequest
from app.schemas.search import SearchScope
from app.services import evidence as service
from tests.unit.test_agent_trial_store import setup_trial


async def test_pinned_raw_excerpt_paging_scope_idempotency_and_restart(
    async_sqlite_session, tmp_path
):
    db = async_sqlite_session
    _, _, sessions, docs = await setup_trial(db, tmp_path)
    doc_ids = [d.id for d in docs]
    body = EvidenceSearch(
        query="method", scope=SearchScope(type="documents", document_ids=[docs[0].id])
    )
    op = await service.create_search(db, body, "key1")
    await db.commit()
    repeated = await service.create_search(db, body, "key1")
    assert repeated.id == op.id
    context_id = op.context_id
    with pytest.raises(ConflictError, match="Operation conflicts"):
        await service.create_search(db, EvidenceSearch(query="other"), "key1")
    await db.rollback()
    chunk = await db.scalar(
        select(Chunk).where(Chunk.document_id == doc_ids[0]).order_by(Chunk.chunk_index)
    )
    chunk_id, original = chunk.id, chunk.raw_content
    async with sessions() as reopened:
        result = await service.read_chunks(
            reopened,
            EvidenceChunks(
                context_id=context_id, requests=[ChunkRead(chunk_id=chunk_id, max_chars=5)]
            ),
        )
        assert result.results[0].raw_content == original[:5]
        assert result.results[0].next_start_char == 5
        assert result.results[0].truncated
        continued = await service.read_chunks(
            reopened,
            EvidenceChunks(
                context_id=context_id, requests=[ChunkRead(chunk_id=chunk_id, start_char=5)]
            ),
        )
        assert continued.results[0].raw_content == original[5:]
        assert continued.results[0].next_start_char is None
        outsider = await reopened.scalar(select(Chunk).where(Chunk.document_id == doc_ids[1]))
        with pytest.raises(ConflictError) as error:
            await service.read_chunks(
                reopened,
                EvidenceChunks(context_id=context_id, requests=[ChunkRead(chunk_id=outsider.id)]),
            )
        assert error.value.code == "SCOPE_VIOLATION"
        with pytest.raises(NotFoundError):
            await service.read_chunks(
                reopened,
                EvidenceChunks(context_id=context_id, requests=[ChunkRead(chunk_id=uuid.uuid4())]),
            )


async def test_expiry_releases_snapshot_fk_and_invalidates_cached_results(
    async_sqlite_session, tmp_path
):
    db = async_sqlite_session
    _, _, _, docs = await setup_trial(db, tmp_path)
    op = await service.create_search(db, EvidenceSearch(query="x"), "expiry")
    op_id, context_id = op.id, op.context_id
    item = await db.get(EvidenceContext, context_id)
    snapshot_id = item.snapshot_id
    item.created_at = datetime.now(UTC) - timedelta(hours=2)
    item.expires_at = datetime.now(UTC) - timedelta(hours=1)
    await db.commit()
    with pytest.raises(ConflictError) as error:
        await service.get_operation(db, op.id)
    assert error.value.code == "CONTEXT_EXPIRED"
    await db.rollback()
    await service.cleanup_contexts(db)
    await db.commit()
    item = await db.get(EvidenceContext, context_id)
    assert item.snapshot_id is None
    assert item.status == "expired"
    assert (await db.get(EvidenceOperation, op_id)).status == "cancelled"
    assert await db.get(IndexSnapshot, snapshot_id) is not None


async def test_document_invalidation_cancels_queued_search_and_rejects_replay(
    async_sqlite_session, tmp_path
):
    db = async_sqlite_session
    _, _, _, docs = await setup_trial(db, tmp_path)
    op = await service.create_search(db, EvidenceSearch(query="x"), "invalidate")
    await db.commit()
    before = op.fencing_token
    await service.invalidate_document_contexts(db, docs[0].id)
    await db.commit()
    assert op.status == "cancelled"
    assert op.fencing_token > before
    item = await db.get(EvidenceContext, op.context_id)
    assert item.snapshot_id is None
    with pytest.raises(ConflictError) as error:
        await service.get_operation(db, op.id)
    assert error.value.code == "SOURCE_INVALIDATED"


async def test_cancel_is_idempotent_and_scope_catalog_is_literal(async_sqlite_session, tmp_path):
    db = async_sqlite_session
    _, _, _, docs = await setup_trial(db, tmp_path)
    listing = await service.list_papers(
        db, PaperListRequest(scope=SearchScope(type="documents", document_ids=[docs[0].id]))
    )
    assert [r.document_id for r in listing.items] == [docs[0].id]
    assert (await service.list_papers(db, PaperListRequest(query="%"))).items == []
    op = await service.create_search(db, EvidenceSearch(query="x"), "cancel")
    await db.commit()
    await service.get_operation(db, op.id, cancel=True)
    await db.commit()
    fence = op.fencing_token
    await service.get_operation(db, op.id, cancel=True)
    assert op.fencing_token == fence
    assert op.status == "cancelled"


async def test_context_and_operation_constraints(async_sqlite_session, tmp_path):
    db = async_sqlite_session
    await setup_trial(db, tmp_path)
    op = await service.create_search(db, EvidenceSearch(query="x"), "constraints")
    await db.commit()
    op.status = "invalid"
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()
    # The active context itself is an actual FK retention lease, even if the
    # snapshot has no remaining SystemState/Agent references.
    item = await db.scalar(select(EvidenceContext))
    assert item.snapshot_id is not None

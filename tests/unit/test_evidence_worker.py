from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.core.config import get_settings
from app.schemas.evidence import EvidenceSearch
from app.schemas.search import SearchScope
from app.services import evidence as service
from app.services import evidence_queue as queue
from app.workers import evidence as worker
from tests.unit.test_agent_trial_store import setup_trial


async def test_worker_runs_pinned_search_without_generator_and_preserves_empty_results(
    async_sqlite_session, tmp_path, monkeypatch
):
    db = async_sqlite_session
    _, tools, sessions, docs = await setup_trial(db, tmp_path)
    monkeypatch.setattr(worker, "get_sessionmaker", lambda: sessions)
    monkeypatch.setattr(worker, "get_embedding_provider", lambda: tools.embedding)
    monkeypatch.setattr(
        worker, "get_settings", lambda: get_settings().model_copy(update={"storage_dir": tmp_path})
    )
    op = await service.create_search(
        db,
        EvidenceSearch(
            query="testing retrieval",
            scope=SearchScope(type="documents", document_ids=[docs[0].id]),
        ),
        "worker",
    )
    await db.commit()
    await worker.evidence_search({}, str(op.id))
    await db.refresh(op)
    assert op.status == "succeeded"
    assert {r["document_id"] for r in op.result["results"]} == {str(docs[0].id)}
    assert all(r["raw_content"] for r in op.result["results"])
    assert op.attempts == 1
    old_fence = op.fencing_token
    await db.commit()
    await worker.evidence_search({}, str(op.id))
    await db.refresh(op)
    assert op.fencing_token == old_fence

    from app.services.arq_enqueuer import FakeEnqueuer
    from app.services.document import DocumentService
    from app.services.ingestion import run_ingest
    from tests.unit.test_ingestion_embedding import _FakeParser, _RealChunker

    pinned_version = op.result["results"][0]["document_version_id"]
    job = await DocumentService(get_settings(), FakeEnqueuer()).request_reindex(db, docs[0].id)
    await run_ingest(
        db,
        job,
        docs[0],
        parser=_FakeParser(),
        chunker=_RealChunker(n_chunks=3, session=db),
        embedding_provider=tools.embedding,
        indexes_dir=tmp_path / "indexes",
    )
    await db.commit()
    followup = await service.create_search(
        db,
        EvidenceSearch(query="testing retrieval", context_id=op.context_id),
        "old-context-after-reindex",
    )
    await db.commit()
    await worker.evidence_search({}, str(followup.id))
    await db.refresh(followup)
    assert followup.status == "succeeded"
    assert followup.result["results"]
    assert {r["document_version_id"] for r in followup.result["results"]} == {pinned_version}

    async def empty(*args, **kwargs):
        return SimpleNamespace(results=[], degraded_reasons=["TEST_EMPTY"])

    monkeypatch.setattr(worker, "search_corpus", empty)
    second = await service.create_search(
        db, EvidenceSearch(query="not present", context_id=op.context_id), "empty"
    )
    await db.commit()
    await worker.evidence_search({}, str(second.id))
    await db.refresh(second)
    assert second.status == "succeeded"
    assert second.result["results"] == []
    assert second.result["degraded_reasons"] == ["TEST_EMPTY"]


async def test_cancel_fences_late_worker_result(async_sqlite_session, tmp_path, monkeypatch):
    db = async_sqlite_session
    _, tools, sessions, _ = await setup_trial(db, tmp_path)
    monkeypatch.setattr(worker, "get_sessionmaker", lambda: sessions)
    monkeypatch.setattr(worker, "get_embedding_provider", lambda: tools.embedding)
    op = await service.create_search(db, EvidenceSearch(query="testing"), "cancel-during")
    op_id = op.id
    await db.commit()

    async def cancel_during_search(*args, **kwargs):
        async with sessions() as cancel_db, cancel_db.begin():
            await service.get_operation(cancel_db, op_id, cancel=True)
        return SimpleNamespace(results=[], degraded_reasons=[])

    monkeypatch.setattr(worker, "search_corpus", cancel_during_search)
    await worker.evidence_search({}, str(op_id))
    await db.refresh(op)
    assert op.status == "cancelled"
    assert op.result is None
    assert op.fencing_token == 2


async def test_dispatch_reconciles_lease_once_without_resetting_deadline(
    async_sqlite_session, tmp_path, monkeypatch
):
    db = async_sqlite_session
    _, _, sessions, _ = await setup_trial(db, tmp_path)
    monkeypatch.setattr(queue, "get_sessionmaker", lambda: sessions)
    op = await service.create_search(db, EvidenceSearch(query="testing"), "lease")
    original_deadline = op.deadline
    op.status, op.attempts, op.fencing_token = "running", 1, 1
    op.lease_expiry = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()

    class Redis:
        def __init__(self):
            self.enqueued = []

        async def enqueue_job(self, *args, **kwargs):
            self.enqueued.append((args, kwargs))

    redis = Redis()
    await queue.dispatch_evidence({"redis": redis})
    await db.refresh(op)
    assert op.status == "queued"
    assert service.utc(op.deadline) == service.utc(original_deadline)
    assert op.fencing_token == 2
    assert len(redis.enqueued) == 1
    op.status, op.attempts = "running", 2
    op.lease_expiry = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()
    await queue.dispatch_evidence({"redis": redis})
    await db.refresh(op)
    assert op.status == "failed"
    assert op.error_code == "WORKER_LOST"
    assert op.result is None

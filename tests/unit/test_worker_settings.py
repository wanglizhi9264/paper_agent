from app.workers.tasks import functions


def test_worker_registers_every_enqueued_ingestion_name() -> None:
    assert {function.name for function in functions} == {
        "ingestion:ingest",
        "ingestion:reindex",
        "ingestion:delete_cleanup",
    }

from __future__ import annotations

import app.models  # noqa: F401 - registers models
from app.db.base import Base


def test_all_expected_tables_registered() -> None:
    expected = {
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
    }
    assert expected.issubset(set(Base.metadata.tables))


def test_chunk_unique_constraint_present() -> None:
    chunks = Base.metadata.tables["chunks"]
    cons = {c.name for c in chunks.constraints}
    assert "uq_chunk_version_index" in cons


def test_collection_name_unique() -> None:
    collections = Base.metadata.tables["collections"]
    cons = {c.name for c in collections.constraints}
    assert any("name" in str(c) and "unique" in str(c).lower() for c in collections.constraints)
    assert "ck_collection_name_length" in cons


def test_collection_documents_composite_pk() -> None:
    cd = Base.metadata.tables["collection_documents"]
    pk_cols = {c.name for c in cd.primary_key.columns}
    assert pk_cols == {"collection_id", "document_id"}


def test_document_cascade_fks() -> None:
    documents = Base.metadata.tables["documents"]
    # No FK columns directly on documents except the deferred active version.
    fk_targets = {fk.target_fullname for fk in documents.foreign_keys}
    assert "document_versions.id" in fk_targets


def test_chunks_self_fk_and_version_fk() -> None:
    chunks = Base.metadata.tables["chunks"]
    fk_targets = {fk.target_fullname for fk in chunks.foreign_keys}
    assert "documents.id" in fk_targets
    assert "document_versions.id" in fk_targets
    assert "chunks.id" in fk_targets  # self-ref for parent/chapter


def test_message_cascade_to_session() -> None:
    messages = Base.metadata.tables["messages"]
    fks = {fk.target_fullname: fk for fk in messages.foreign_keys}
    assert "sessions.id" in fks
    assert fks["sessions.id"].ondelete == "CASCADE"


def test_collection_document_cascade_on_document_delete() -> None:
    cd = Base.metadata.tables["collection_documents"]
    fks = {fk.parent.name: fk for fk in cd.foreign_keys}
    assert fks["document_id"].ondelete == "CASCADE"
    assert fks["collection_id"].ondelete == "CASCADE"


def test_retrieval_log_set_null_on_delete() -> None:
    log = Base.metadata.tables["retrieval_logs"]
    fks = {fk.parent.name: fk for fk in log.foreign_keys}
    assert fks["session_id"].ondelete == "SET NULL"
    assert fks["message_id"].ondelete == "SET NULL"


def test_system_state_singleton_check() -> None:
    ss = Base.metadata.tables["system_state"]
    cons = {c.name for c in ss.constraints}
    assert "ck_system_state_singleton" in cons


def test_faiss_id_sequence_registered() -> None:
    from app.models.chunk import FAISS_ID_SEQ

    assert FAISS_ID_SEQ.name == "faiss_id_seq"
    assert FAISS_ID_SEQ.start == 1


def test_check_constraints_present() -> None:
    documents = Base.metadata.tables["documents"]
    names = {c.name for c in documents.constraints if hasattr(c, "name")}
    assert "ck_document_file_size_range" in names
    assert "ck_document_extension_allowlist" in names
    assert "ck_document_status_enum" in names

    jobs = Base.metadata.tables["ingestion_jobs"]
    job_names = {c.name for c in jobs.constraints if hasattr(c, "name")}
    assert "ck_job_progress_range" in job_names
    assert "ck_job_attempt_min" in job_names

    chunks = Base.metadata.tables["chunks"]
    chunk_names = {c.name for c in chunks.constraints if hasattr(c, "name")}
    assert "ck_chunk_faiss_id_nonneg" in chunk_names
    assert "ck_chunk_page_order" in chunk_names

    versions = Base.metadata.tables["document_versions"]
    assert {
        "parser_id",
        "parser_signature",
        "ir_schema_version",
        "ir_path",
        "ir_sha256",
        "parse_quality",
    }.issubset(versions.c.keys())
    version_names = {c.name for c in versions.constraints if hasattr(c, "name")}
    assert "ck_docversion_ir_schema_positive" in version_names

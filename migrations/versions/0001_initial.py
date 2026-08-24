"""initial schema: documents, collections, jobs, versions, chunks, snapshots, sessions, messages, logs

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-24

Phase 1 — all MVP tables per docs/spec.md §9. Circular foreign keys
(documents.active_document_version_id, system_state.active_index_snapshot_id)
are added via ALTER TABLE after all tables are created.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Sequence for non-negative FAISS ids ---
    op.execute("CREATE SEQUENCE faiss_id_seq START 1 MINVALUE 1 INCREMENT 1")

    # --- documents (no circular FK yet) ---
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("stored_filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("extension", sa.String(10), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="uploaded"),
        sa.Column("status_message", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("character_count", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_document_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parser_version", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("file_size BETWEEN 1 AND 104857600", name="ck_document_file_size_range"),
        sa.CheckConstraint("extension IN ('pdf','docx','md')", name="ck_document_extension_allowlist"),
        sa.CheckConstraint(
            "status IN ('uploaded','queued','parsing','chunking','embedding',"
            "'indexing','ready','failed','deleting','deleted')",
            name="ck_document_status_enum",
        ),
    )
    op.create_index("ix_documents_status", "documents", ["status"])
    op.create_index("ix_documents_sha256", "documents", ["sha256"])
    op.create_index("ix_documents_created_at", "documents", ["created_at"])

    # --- document_versions ---
    op.create_table(
        "document_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE", name="fk_version_document"),
            nullable=False,
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="building"),
        sa.Column("parser_version", sa.String(100), nullable=True),
        sa.Column("chunk_config", postgresql.JSONB(), nullable=False),
        sa.Column("embedding_model_id", sa.String(200), nullable=True),
        sa.Column("embedding_revision", sa.String(64), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("embedding_signature", sa.String(128), nullable=True),
        sa.Column("analyzer_config", postgresql.JSONB(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("character_count", sa.Integer(), nullable=True),
        sa.Column("stats", postgresql.JSONB(), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('building','ready','superseded','failed')",
            name="ck_docversion_status_enum",
        ),
        sa.CheckConstraint("chunk_count >= 0", name="ck_docversion_chunk_count"),
        sa.CheckConstraint(
            "(embedding_dimension IS NULL OR embedding_dimension > 0)",
            name="ck_docversion_dim_positive",
        ),
    )
    op.create_index("ix_docversion_document_id", "document_versions", ["document_id"])
    op.create_index("ix_docversion_status", "document_versions", ["status"])
    op.create_index("ix_docversion_signature", "document_versions", ["embedding_signature"])

    # --- chunks ---
    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE", name="fk_chunk_document"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE", name="fk_chunk_version"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("parent_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("chapter_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("section_path", postgresql.JSONB(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("line_start", sa.Integer(), nullable=True),
        sa.Column("line_end", sa.Integer(), nullable=True),
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.Column("retrieval_content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("character_count", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("faiss_id", sa.BigInteger(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("document_version_id", "chunk_index", name="uq_chunk_version_index"),
        sa.CheckConstraint("chunk_index >= 0", name="ck_chunk_index_nonneg"),
        sa.CheckConstraint("character_count >= 0", name="ck_chunk_char_count"),
        sa.CheckConstraint("kind IN ('text','title','table','code','chapter')", name="ck_chunk_kind_enum"),
        sa.CheckConstraint(
            "(page_start IS NULL OR page_end IS NULL OR page_start <= page_end)",
            name="ck_chunk_page_order",
        ),
        sa.CheckConstraint(
            "(line_start IS NULL OR line_end IS NULL OR line_start <= line_end)",
            name="ck_chunk_line_order",
        ),
        sa.CheckConstraint("(faiss_id IS NULL OR faiss_id >= 0)", name="ck_chunk_faiss_id_nonneg"),
    )
    # self-referential FKs added after table creation
    op.create_foreign_key(
        "fk_chunk_parent",
        "chunks",
        "chunks",
        ["parent_chunk_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_chunk_chapter",
        "chunks",
        "chunks",
        ["chapter_chunk_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_chunk_document_id", "chunks", ["document_id"])
    op.create_index("ix_chunk_version_id", "chunks", ["document_version_id"])
    op.create_index("ix_chunk_content_hash", "chunks", ["content_hash"])
    op.create_index("ix_chunk_chapter", "chunks", ["chapter_chunk_id"])
    op.create_index("ix_chunk_faiss_id", "chunks", ["faiss_id"], unique=True)

    # --- collections ---
    op.create_table(
        "collections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.String(1000), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 120", name="ck_collection_name_length"),
        sa.CheckConstraint("length(description) <= 1000", name="ck_collection_desc_length"),
    )

    # --- collection_documents ---
    op.create_table(
        "collection_documents",
        sa.Column(
            "collection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("collections.id", ondelete="CASCADE", name="fk_cd_collection"),
            primary_key=True,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE", name="fk_cd_document"),
            primary_key=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_cd_document_id", "collection_documents", ["document_id"])

    # --- ingestion_jobs ---
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE", name="fk_job_document"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name="ck_job_progress_range"),
        sa.CheckConstraint("attempt >= 1", name="ck_job_attempt_min"),
        sa.CheckConstraint("kind IN ('ingest','reindex','delete_cleanup')", name="ck_job_kind_enum"),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')", name="ck_job_status_enum"
        ),
        sa.CheckConstraint(
            "stage IN ('queued','parsing','chunking','embedding','indexing','finalizing')",
            name="ck_job_stage_enum",
        ),
        sa.CheckConstraint(
            "(started_at IS NULL OR finished_at IS NULL OR started_at <= finished_at)",
            name="ck_job_time_order",
        ),
    )
    op.create_index("ix_jobs_document_id", "ingestion_jobs", ["document_id"])
    op.create_index("ix_jobs_status", "ingestion_jobs", ["status"])
    op.create_index("ix_jobs_created_at", "ingestion_jobs", ["created_at"])

    # --- index_snapshots ---
    op.create_table(
        "index_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="building"),
        sa.Column("embedding_signature", sa.String(128), nullable=True),
        sa.Column("faiss_path", sa.Text(), nullable=True),
        sa.Column("bm25_path", sa.Text(), nullable=True),
        sa.Column("manifest_sha256", sa.String(64), nullable=True),
        sa.Column("manifest", postgresql.JSONB(), nullable=True),
        sa.Column("document_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_faiss_id", sa.Integer(), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('building','active','superseded','failed')", name="ck_snapshot_status_enum"
        ),
        sa.CheckConstraint("document_count >= 0", name="ck_snapshot_doc_count"),
        sa.CheckConstraint("chunk_count >= 0", name="ck_snapshot_chunk_count"),
        sa.CheckConstraint("(max_faiss_id IS NULL OR max_faiss_id >= 0)", name="ck_snapshot_max_faiss"),
    )
    op.create_index("ix_snapshot_status", "index_snapshots", ["status"])
    op.create_index("ix_snapshot_signature", "index_snapshots", ["embedding_signature"])

    # --- system_state (singleton) ---
    op.create_table(
        "system_state",
        sa.Column("id", sa.Integer(), primary_key=True, server_default="1"),
        sa.Column("active_index_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("id = 1", name="ck_system_state_singleton"),
    )

    # --- sessions ---
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False, server_default="Untitled"),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("scope_payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("scope_type IN ('all','documents','collection')", name="ck_session_scope_type"),
        sa.CheckConstraint("length(title) BETWEEN 1 AND 200", name="ck_session_title_length"),
    )
    op.create_index("ix_sessions_created_at", "sessions", ["created_at"])

    # --- messages ---
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE", name="fk_message_session"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="complete"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("citations", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('user','assistant','system')", name="ck_message_role_enum"),
        sa.CheckConstraint("status IN ('complete','interrupted')", name="ck_message_status_enum"),
    )
    op.create_index("ix_messages_session_id", "messages", ["session_id"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])

    # --- retrieval_logs ---
    op.create_table(
        "retrieval_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="SET NULL", name="fk_retrlog_session"),
            nullable=True,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("messages.id", ondelete="SET NULL", name="fk_retrlog_message"),
            nullable=True,
        ),
        sa.Column("original_query", sa.Text(), nullable=False),
        sa.Column("rewritten_query", sa.Text(), nullable=True),
        sa.Column("rewrite_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scope", postgresql.JSONB(), nullable=False),
        sa.Column("params_snapshot", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("model_versions", postgresql.JSONB(), nullable=True),
        sa.Column("bm25_candidates", postgresql.JSONB(), nullable=True),
        sa.Column("dense_candidates", postgresql.JSONB(), nullable=True),
        sa.Column("rrf_candidates", postgresql.JSONB(), nullable=True),
        sa.Column("rerank_candidates", postgresql.JSONB(), nullable=True),
        sa.Column("expanded_candidates", postgresql.JSONB(), nullable=True),
        sa.Column("final_context", postgresql.JSONB(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("citations", postgresql.JSONB(), nullable=True),
        sa.Column("degraded_reasons", postgresql.JSONB(), nullable=True),
        sa.Column("timings_ms", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("length(original_query) BETWEEN 1 AND 4000", name="ck_retrlog_query_length"),
    )
    op.create_index("ix_retrlog_session_id", "retrieval_logs", ["session_id"])
    op.create_index("ix_retrlog_created_at", "retrieval_logs", ["created_at"])

    # --- Deferred circular FKs (added after all tables exist) ---
    op.create_foreign_key(
        "fk_document_active_version",
        "documents",
        "document_versions",
        ["active_document_version_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_system_active_snapshot",
        "system_state",
        "index_snapshots",
        ["active_index_snapshot_id"],
        ["id"],
    )

    # Seed the singleton system_state row.
    op.execute("INSERT INTO system_state (id) VALUES (1) ON CONFLICT DO NOTHING")


def downgrade() -> None:
    op.drop_constraint("fk_system_active_snapshot", "system_state", type_="foreignkey")
    op.drop_constraint("fk_document_active_version", "documents", type_="foreignkey")
    op.drop_table("retrieval_logs")
    op.drop_table("messages")
    op.drop_table("sessions")
    op.drop_table("system_state")
    op.drop_table("index_snapshots")
    op.drop_table("ingestion_jobs")
    op.drop_table("collection_documents")
    op.drop_table("collections")
    op.drop_table("chunks")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.execute("DROP SEQUENCE IF EXISTS faiss_id_seq")

"""agent runtime persistence

Revision ID: 0005_agent_runtime
Revises: 0004
Create Date: 2026-10-16 11:53:03.345754

"""

from __future__ import annotations
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0005_agent_runtime"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "session_summaries",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("covered_through_sequence", sa.Integer(), nullable=False),
        sa.Column(
            "summary_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("model_signature", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("user_message_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "pins",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("recovery_count", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("lease_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("final_message_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded','failed','cancelled')) = (finished_at IS NOT NULL)",
            name="ck_agent_finished",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','interrupted','succeeded','failed','cancelled')",
            name="ck_agent_status",
        ),
        sa.CheckConstraint("length(query) BETWEEN 1 AND 4000", name="ck_agent_query"),
        sa.CheckConstraint(
            "revision >= 0 AND event_sequence >= 0 AND fencing_token >= 0 AND recovery_count BETWEEN 0 AND 2",
            name="ck_agent_counters",
        ),
        sa.ForeignKeyConstraint(["final_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_message_id"], ["messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "uq_agent_session_active",
        "agent_runs",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running','interrupted')"),
        sqlite_where=sa.text("status IN ('queued','running','interrupted')"),
    )
    op.create_table(
        "agent_checkpoints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("graph_version", sa.String(length=40), nullable=False),
        sa.Column("last_event_sequence", sa.Integer(), nullable=False),
        sa.Column(
            "state_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.CheckConstraint("revision >= 0"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "revision"),
    )
    op.create_table(
        "agent_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column(
            "payload",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence > 0"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence"),
    )
    op.create_table(
        "agent_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column(
            "provenance",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "chunk_id"),
    )
    op.create_table(
        "agent_run_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("dispatched", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "generation"),
    )
    op.create_table(
        "agent_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("call_id", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=64), nullable=False),
        sa.Column("tool_version", sa.String(length=20), nullable=False),
        sa.Column("args_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "result_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
        sa.CheckConstraint("status IN ('running','succeeded','failed')"),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "call_id"),
        sa.UniqueConstraint("run_id", "sequence"),
    )
    op.add_column("messages", sa.Column("run_id", sa.Uuid(), nullable=True))
    op.add_column("messages", sa.Column("message_sequence", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_message_agent_run","messages","agent_runs",["run_id"],["id"],ondelete="SET NULL")
    op.add_column("agent_runs",sa.Column("snapshot_id",sa.Uuid(),nullable=True))
    op.create_foreign_key("fk_agent_snapshot_lease","agent_runs","index_snapshots",["snapshot_id"],["id"],ondelete="RESTRICT")
    op.execute("WITH ordered AS (SELECT id,row_number() OVER(PARTITION BY session_id ORDER BY created_at,id) AS seq FROM messages) UPDATE messages SET message_sequence=ordered.seq FROM ordered WHERE messages.id=ordered.id")
    op.create_index(
        "uq_message_run_complete",
        "messages",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("role='assistant' AND status='complete' AND run_id IS NOT NULL"),
        sqlite_where=sa.text("role='assistant' AND status='complete' AND run_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_constraint("fk_message_agent_run","messages",type_="foreignkey")
    op.drop_index(
        "uq_message_run_complete",
        table_name="messages",
        postgresql_where=sa.text("role='assistant' AND status='complete' AND run_id IS NOT NULL"),
        sqlite_where=sa.text("role='assistant' AND status='complete' AND run_id IS NOT NULL"),
    )
    op.drop_column("messages", "message_sequence")
    op.drop_column("messages", "run_id")
    op.drop_table("agent_steps")
    op.drop_table("agent_run_outbox")
    op.drop_table("agent_evidence")
    op.drop_table("agent_events")
    op.drop_table("agent_checkpoints")
    op.drop_index(
        "uq_agent_session_active",
        table_name="agent_runs",
        postgresql_where=sa.text("status IN ('queued','running','interrupted')"),
        sqlite_where=sa.text("status IN ('queued','running','interrupted')"),
    )
    op.drop_table("agent_runs")
    op.drop_table("session_summaries")

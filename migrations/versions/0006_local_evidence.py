"""Pinned local MCP evidence contexts and fenced read operations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_local_evidence"
down_revision = "0005_agent_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("evidence_contexts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("snapshot_id", sa.Uuid(), sa.ForeignKey("index_snapshots.id", ondelete="RESTRICT")),
        sa.Column("pins", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('active','expired','invalidated')", name="ck_evidence_context_status"),
        sa.CheckConstraint("(status = 'active') = (snapshot_id IS NOT NULL)", name="ck_evidence_context_snapshot"),
        sa.CheckConstraint("expires_at > created_at", name="ck_evidence_context_time"))
    op.create_index("ix_evidence_context_expiry", "evidence_contexts", ["status", "expires_at"])
    op.create_table("evidence_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("context_id", sa.Uuid(), sa.ForeignKey("evidence_contexts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("idempotency_key", sa.String(128), unique=True, nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("request", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", postgresql.JSONB()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expiry", sa.DateTime(timezone=True)),
        sa.Column("dispatched_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed','cancelled')", name="ck_evidence_op_status"),
        sa.CheckConstraint("attempts BETWEEN 0 AND 2 AND fencing_token >= 0", name="ck_evidence_op_counters"),
        sa.CheckConstraint("(status IN ('succeeded','failed','cancelled')) = (finished_at IS NOT NULL)", name="ck_evidence_op_finished"),
        sa.CheckConstraint("deadline > created_at", name="ck_evidence_op_time"))
    op.create_index("ix_evidence_op_pending", "evidence_operations", ["status", "deadline"])
    op.create_index("ix_evidence_op_context", "evidence_operations", ["context_id"])


def downgrade() -> None:
    op.drop_table("evidence_operations")
    op.drop_table("evidence_contexts")

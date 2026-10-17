"""Add durable, CLI-only paper agent trial journal.

Revision ID: 0004
Revises: 0003
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003_failed_at_tz"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_trial_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("pins", postgresql.JSONB(), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("events", postgresql.JSONB(), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("final_message_id", sa.Uuid(), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('running','interrupted','succeeded','failed')", name="ck_agent_trial_status"),
        sa.CheckConstraint("length(query) BETWEEN 1 AND 4000", name="ck_agent_trial_query"),
    )
    op.create_index("uq_agent_trial_active_session", "agent_trial_runs", ["session_id"], unique=True,
                    postgresql_where=sa.text("status IN ('running','interrupted')"))


def downgrade() -> None:
    op.drop_table("agent_trial_runs")

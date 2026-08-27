"""repair document version failure timestamp timezone

Revision ID: 0003_failed_at_tz
Revises: 0002_pdf_ingestion_v2
Create Date: 2026-08-26

Fresh databases created by 0001 already use TIMESTAMP WITH TIME ZONE. This
migration also repairs older local databases that were created from ORM
metadata while the DocumentVersion mapping omitted ``timezone=True``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_failed_at_tz"
down_revision: str | None = "0002_pdf_ingestion_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'document_versions'
                  AND column_name = 'failed_at'
                  AND data_type = 'timestamp without time zone'
            ) THEN
                ALTER TABLE document_versions
                ALTER COLUMN failed_at TYPE TIMESTAMP WITH TIME ZONE
                USING failed_at AT TIME ZONE 'UTC';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # 0001 already defines this column as timezone-aware, so the repair must
    # not recreate the historical ORM drift when returning to 0002.
    pass

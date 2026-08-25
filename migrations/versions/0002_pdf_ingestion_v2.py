"""add Canonical IR metadata to document versions

Revision ID: 0002_pdf_ingestion_v2
Revises: 0001_initial
Create Date: 2026-08-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_pdf_ingestion_v2"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("document_versions", sa.Column("parser_id", sa.String(32), nullable=True))
    op.add_column(
        "document_versions", sa.Column("parser_signature", sa.String(64), nullable=True)
    )
    op.add_column("document_versions", sa.Column("ir_schema_version", sa.Integer(), nullable=True))
    op.add_column("document_versions", sa.Column("ir_path", sa.Text(), nullable=True))
    op.add_column("document_versions", sa.Column("ir_sha256", sa.String(64), nullable=True))
    op.add_column(
        "document_versions",
        sa.Column("parse_quality", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index(
        "ix_docversion_parser_signature",
        "document_versions",
        ["parser_signature"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_docversion_ir_schema_positive",
        "document_versions",
        "ir_schema_version IS NULL OR ir_schema_version > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_docversion_ir_schema_positive", "document_versions", type_="check"
    )
    op.drop_index("ix_docversion_parser_signature", table_name="document_versions")
    op.drop_column("document_versions", "parse_quality")
    op.drop_column("document_versions", "ir_sha256")
    op.drop_column("document_versions", "ir_path")
    op.drop_column("document_versions", "ir_schema_version")
    op.drop_column("document_versions", "parser_signature")
    op.drop_column("document_versions", "parser_id")

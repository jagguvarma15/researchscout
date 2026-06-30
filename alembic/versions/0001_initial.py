"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-29

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "papers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=False),
        sa.Column("authors", JSONB(), nullable=False),
        sa.Column("categories", JSONB(), nullable=False),
        sa.Column("venue", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("pdf_url", sa.Text(), nullable=True),
        sa.Column("full_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "row_updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "paper_external_ids",
        sa.Column("scheme", sa.String(), primary_key=True),
        sa.Column("value", sa.String(), primary_key=True),
        sa.Column(
            "paper_id",
            sa.String(),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("ix_paper_external_ids_paper_id", "paper_external_ids", ["paper_id"])
    op.create_table(
        "raw_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("payload", JSONB(), nullable=False),
    )
    op.create_table(
        "ingest_state",
        sa.Column("source", sa.String(), primary_key=True),
        sa.Column("cursor", sa.String(), nullable=True),
        sa.Column("last_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("ingest_state")
    op.drop_table("raw_items")
    op.drop_index("ix_paper_external_ids_paper_id", table_name="paper_external_ids")
    op.drop_table("paper_external_ids")
    op.drop_table("papers")

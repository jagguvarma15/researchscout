"""paper chunks

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-29

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import HALFVEC

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_chunks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "paper_id",
            sa.String(),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", HALFVEC(384), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_paper_chunks_paper", "paper_chunks", ["paper_id", "model_id", "chunk_index"]
    )
    op.execute(
        "CREATE INDEX ix_paper_chunks_hnsw ON paper_chunks "
        "USING hnsw (embedding halfvec_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_paper_chunks_hnsw", table_name="paper_chunks")
    op.drop_index("ix_paper_chunks_paper", table_name="paper_chunks")
    op.drop_table("paper_chunks")

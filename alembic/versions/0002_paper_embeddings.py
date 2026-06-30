"""paper embeddings

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-30

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_embeddings",
        sa.Column(
            "paper_id",
            sa.String(),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("model_id", sa.String(), primary_key=True),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.execute(
        "CREATE INDEX ix_paper_embeddings_hnsw ON paper_embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_index("ix_paper_embeddings_hnsw", table_name="paper_embeddings")
    op.drop_table("paper_embeddings")

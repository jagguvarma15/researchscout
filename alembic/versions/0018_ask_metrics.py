"""ask metrics table

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-31

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ask_metrics",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "asked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("surface", sa.String(), nullable=False),
        sa.Column("question", sa.String(200), nullable=False),
        sa.Column("retrieved", sa.Integer(), nullable=False),
        sa.Column("best_relevance", sa.Float(), nullable=True),
        sa.Column("found", sa.Boolean(), nullable=False),
        sa.Column("retrieve_ms", sa.Integer(), nullable=True),
        sa.Column("rerank_ms", sa.Integer(), nullable=True),
        sa.Column("llm_ms", sa.Integer(), nullable=True),
        sa.Column("total_ms", sa.Integer(), nullable=False),
    )
    op.create_index("ix_ask_metrics_asked_at", "ask_metrics", ["asked_at"])


def downgrade() -> None:
    op.drop_index("ix_ask_metrics_asked_at", table_name="ask_metrics")
    op.drop_table("ask_metrics")

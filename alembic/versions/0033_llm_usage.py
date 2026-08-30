"""llm usage ledger, one row per model call

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-29

The daily model budget is the binding constraint of the AI surface, and nothing measured
it: the provider's token counts were discarded on arrival and the call count was a
hand-derived property of the code. Every completion now lands here with its purpose,
model, token counts, latency, and outcome, so "how much quota is left" and "what spent
it" become queries instead of guesses.

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.BigInteger, autoincrement=True, nullable=False),
        sa.Column(
            "called_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("prompt_tokens", sa.Integer, nullable=True),
        sa.Column("completion_tokens", sa.Integer, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=False),
        sa.Column("outcome", sa.String(10), nullable=False),
        sa.Column("detail", sa.String(200), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_usage_called_at", "llm_usage", ["called_at"])
    op.create_index("ix_llm_usage_purpose_called", "llm_usage", ["purpose", "called_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_usage_purpose_called", table_name="llm_usage")
    op.drop_index("ix_llm_usage_called_at", table_name="llm_usage")
    op.drop_table("llm_usage")

"""digest kind and llm_ok columns

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-02

Weekly digests and daily reports have always shared one table and one flat slug namespace,
leaving the kind to be inferred by parsing the slug - workable for rendering, useless for
filtering or paging by kind. The column makes the distinction queryable; the backfill derives
it from the only shape a daily slug can take (a plain date). llm_ok records whether the weekly
prose came from the model or the deterministic fallback - computed on every build since the
quota-degradation work but discarded at write time, so a reader could never tell a fallback
issue from a real one. Historical fallbacks are identifiable by the fallback body's fixed
opening line.

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "digests",
        sa.Column("kind", sa.String, nullable=False, server_default="weekly"),
    )
    op.add_column(
        "digests",
        sa.Column("llm_ok", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.execute(r"UPDATE digests SET kind = 'daily' WHERE slug ~ '^\d{4}-\d{2}-\d{2}$'")
    op.execute(
        "UPDATE digests SET llm_ok = false "
        "WHERE kind = 'weekly' AND body LIKE 'The digest model was unavailable%'"
    )
    op.create_index("ix_digests_kind_period", "digests", ["kind", "period_end"])


def downgrade() -> None:
    op.drop_index("ix_digests_kind_period")
    op.drop_column("digests", "llm_ok")
    op.drop_column("digests", "kind")

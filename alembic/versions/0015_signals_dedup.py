"""signals dedup and unique observation index

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-30

"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pre-dedupe exact duplicates (keep the earliest row) so the unique index can build
    # on real data; legitimate re-observations differ in observed_at and are untouched.
    op.execute(
        "DELETE FROM signals a USING signals b "
        "WHERE a.id > b.id "
        "AND a.paper_id = b.paper_id AND a.type = b.type "
        "AND a.source = b.source AND a.observed_at = b.observed_at"
    )
    op.create_index(
        "uq_signals_observation",
        "signals",
        ["paper_id", "type", "source", "observed_at"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_signals_observation", table_name="signals")

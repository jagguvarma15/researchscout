"""un-tombstone full-text rows the pre-grace batches marked

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-20

Until now a full-text fetch that found nothing wrote the permanent "checked, unavailable"
empty string - including when the miss was a network blip or HTML that arXiv simply had
not rendered yet, which is the normal state the night a paper is announced. Those rows
were wrongly closed. Resetting every tombstone to NULL returns them to the pending queue,
where the new grace rule applies: real text sticks, and only a paper old enough that its
HTML will never appear gets re-tombstoned after its retry.

Honest expectations: pending is ordered published_at DESC and the batch is ~100 a day, so
the practical effect is un-tombstoning recently mismarked papers; the old tail will mostly
never be reached, and what is reached gets exactly one more attempt.

The downgrade is a no-op - the pre-heal state (which rows were '' rather than NULL) is not
recoverable, and re-tombstoning everything would destroy the pending queue it repaired.

"""

from __future__ import annotations

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE papers SET full_text = NULL WHERE full_text = ''")


def downgrade() -> None:
    pass

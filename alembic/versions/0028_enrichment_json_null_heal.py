"""turn JSON-null enrichment columns back into SQL NULL

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-20

upsert_paper bound Python None into the JSONB enrichment columns, and SQLAlchemy's JSON
type serializes that as the JSON 'null' value rather than SQL NULL. Every consumer speaks
SQL NULL: papers_missing_keywords selects IS NULL (so the categorize task would never see
these rows) and the stream producer's enrichment watermark reads IS NOT NULL (so it
counted them as already enriched). One pass turns the imposters back into real NULLs; the
matching upsert fix stops new ones from being written.

"""

from __future__ import annotations

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("keywords", "sections", "labels"):
        op.execute(f"UPDATE papers SET {column} = NULL WHERE {column} = 'null'::jsonb")


def downgrade() -> None:
    pass

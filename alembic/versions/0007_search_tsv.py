"""search tsvector

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-07

"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE papers ADD COLUMN search_tsv tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(title, '')), 'A')
            || setweight(to_tsvector('english', coalesce(abstract, '')), 'B')
        ) STORED
        """
    )
    op.execute("CREATE INDEX ix_papers_search_tsv ON papers USING gin (search_tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_papers_search_tsv")
    op.execute("ALTER TABLE papers DROP COLUMN search_tsv")

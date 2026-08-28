"""index papers.keywords for the keyword facet

Revision ID: 0029
Revises: 0028
Create Date: 2026-08-28

The keyword facet filters with the JSONB any-key operator (?|), which the default
jsonb_ops GIN operator class supports and jsonb_path_ops does not - path_ops only serves
containment, so choosing it here would leave the facet on a sequential scan.

"""

from __future__ import annotations

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX ix_papers_keywords_gin ON papers USING gin (keywords)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_papers_keywords_gin")

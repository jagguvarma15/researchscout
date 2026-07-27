"""paper facets

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-26

"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE papers ADD COLUMN primary_category TEXT")
    op.execute("ALTER TABLE papers ADD COLUMN comment TEXT")
    op.execute("ALTER TABLE papers ADD COLUMN citation_count INTEGER NOT NULL DEFAULT 0")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        ALTER TABLE papers ADD COLUMN author_names text
        GENERATED ALWAYS AS (jsonb_path_query_array(authors, '$[*].name')::text) STORED
        """
    )
    op.execute(
        """
        UPDATE papers SET primary_category = categories->>0
        WHERE primary_category IS NULL AND jsonb_array_length(categories) > 0
        """
    )
    op.execute(
        """
        UPDATE papers SET citation_count = s.value::int
        FROM (
            SELECT DISTINCT ON (paper_id) paper_id, value
            FROM signals WHERE type = 'citation'
            ORDER BY paper_id, observed_at DESC
        ) s
        WHERE papers.id = s.paper_id
        """
    )
    op.execute("CREATE INDEX ix_papers_published_at ON papers (published_at DESC, id)")
    op.execute("CREATE INDEX ix_papers_categories ON papers USING gin (categories)")
    op.execute(
        "CREATE INDEX ix_papers_author_names ON papers USING gin (author_names gin_trgm_ops)"
    )
    op.execute("CREATE INDEX ix_papers_venue ON papers USING gin (venue gin_trgm_ops)")
    op.execute(
        "CREATE INDEX ix_papers_primary_group ON papers (split_part(primary_category, '.', 1))"
    )
    op.execute("CREATE INDEX ix_papers_citation_count ON papers (citation_count DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_papers_citation_count")
    op.execute("DROP INDEX ix_papers_primary_group")
    op.execute("DROP INDEX ix_papers_venue")
    op.execute("DROP INDEX ix_papers_author_names")
    op.execute("DROP INDEX ix_papers_categories")
    op.execute("DROP INDEX ix_papers_published_at")
    op.execute("ALTER TABLE papers DROP COLUMN author_names")
    op.execute("ALTER TABLE papers DROP COLUMN citation_count")
    op.execute("ALTER TABLE papers DROP COLUMN comment")
    op.execute("ALTER TABLE papers DROP COLUMN primary_category")
    # pg_trgm stays installed: other objects may depend on it and CREATE used IF NOT EXISTS.

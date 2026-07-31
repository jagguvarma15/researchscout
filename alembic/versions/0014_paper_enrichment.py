"""paper enrichment columns

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-30

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("papers", sa.Column("keywords", JSONB, nullable=True))
    op.add_column("papers", sa.Column("sections", JSONB, nullable=True))
    op.add_column("papers", sa.Column("labels", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("papers", "labels")
    op.drop_column("papers", "sections")
    op.drop_column("papers", "keywords")

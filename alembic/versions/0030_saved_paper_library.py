"""grow saved papers into a library: status, tags, note

Revision ID: 0030
Revises: 0029
Create Date: 2026-08-28

Reading status defaults to to-read for every existing save - a bookmark was always a
promise to read. Tags and the note are JSONB/text and nullable; the writers omit absent
fields rather than binding None, so these columns never hold the JSON-null imposter
migration 0028 had to heal.

"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "saved_papers",
        sa.Column("status", sa.String(16), nullable=False, server_default="to-read"),
    )
    op.add_column("saved_papers", sa.Column("tags", JSONB, nullable=True))
    op.add_column("saved_papers", sa.Column("note", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("saved_papers", "note")
    op.drop_column("saved_papers", "tags")
    op.drop_column("saved_papers", "status")

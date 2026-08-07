"""an avatar on the account

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-07

The header shows initials computed from the username; the profile now lets an account pick one
of the site's drawn avatars instead. The column stores only the chosen slug - the art itself
lives in the web app, so an unknown value simply falls back to initials and the server never
has to know the current set.

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar")

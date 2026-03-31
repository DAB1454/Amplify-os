"""widen avatar_url to text

Revision ID: a1b2c3d4e567
Revises: f7a8b9c0d123
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e567"
down_revision = "f7a8b9c0d123"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "channel_connections",
        "avatar_url",
        type_=sa.Text(),
        existing_type=sa.String(500),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "channel_connections",
        "avatar_url",
        type_=sa.String(500),
        existing_type=sa.Text(),
        existing_nullable=True,
    )

"""add avatar_url to channel_connections

Revision ID: f7a8b9c0d123
Revises: e6f7a8b9c012
Create Date: 2026-03-31
"""
from alembic import op
import sqlalchemy as sa

revision = "f7a8b9c0d123"
down_revision = "e6f7a8b9c012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "channel_connections",
        sa.Column("avatar_url", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("channel_connections", "avatar_url")

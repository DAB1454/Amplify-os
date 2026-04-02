"""add track_reference to posts

Revision ID: a1b2c3d4e501
Revises: f7a8b9c0d123
Create Date: 2026-04-02
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e501"
down_revision = "f7a8b9c0d123"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("posts", sa.Column("track_reference", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("posts", "track_reference")

"""add goal column to posts

Goal taxonomy: awareness, follow, engage, save, stream, purchase.
Nullable so historical posts and manual posts without explicit intent
continue to work. Drives goal-aware CTA selection, copy guidance, and
analytics segmentation.

Revision ID: d1e2f3a4b567
Revises: c9d8e7f6a543
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa

revision = "d1e2f3a4b567"
down_revision = "c9d8e7f6a543"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column("goal", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("posts", "goal")

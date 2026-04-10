"""add repurposed_from_id to posts

Tracks cross-channel repurposing so the learning layer can compare
same-content performance across platforms.

Revision ID: a1b2c3d4e567
Revises: f8b9c0d1e234
Create Date: 2026-04-10 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e567"
down_revision = "f8b9c0d1e234"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column(
            "repurposed_from_id",
            sa.Uuid(),
            sa.ForeignKey("posts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_posts_repurposed_from_id", "posts", ["repurposed_from_id"])


def downgrade() -> None:
    op.drop_index("ix_posts_repurposed_from_id", table_name="posts")
    op.drop_column("posts", "repurposed_from_id")

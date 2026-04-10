"""add repurposed_from_id to posts

Tracks cross-channel repurposing so the learning layer can compare
same-content performance across platforms.

Revision ID: d2e3f4a5b678
Revises: c7a9e1b2f303
Create Date: 2026-04-10 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "d2e3f4a5b678"
down_revision = "c7a9e1b2f303"
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

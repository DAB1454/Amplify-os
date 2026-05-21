"""add tiktok_post_info to posts

Persists the Content Sharing Guidelines disclosure choices on the
post row so the worker can read them at publish time, not just the
request-scoped HTTP path. Required for scheduled and autopilot
TikTok posts to satisfy TikTok's per-post disclosure requirement
once Direct Post is approved.

Revision ID: d9f0a1b2c345
Revises: c8e9f0a1b234
Create Date: 2026-05-21 14:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "d9f0a1b2c345"
down_revision = "c8e9f0a1b234"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column(
            "tiktok_post_info",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("posts", "tiktok_post_info")

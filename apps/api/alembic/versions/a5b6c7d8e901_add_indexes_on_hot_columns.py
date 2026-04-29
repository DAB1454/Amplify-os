"""Add indexes on frequently queried columns

Posts: status, platform, published_at (used by scan_scheduled, sync_metrics, weekly_analyst)
Channels: platform, is_active (used by refresh_tokens, sync_metrics, backfill_posts)

Revision ID: a5b6c7d8e901
Revises: f4a5b6c7d890
Create Date: 2026-04-29 12:00:00.000000
"""

from alembic import op

revision = "a5b6c7d8e901"
down_revision = "f4a5b6c7d890"


def upgrade() -> None:
    op.create_index("ix_posts_status", "posts", ["status"])
    op.create_index("ix_posts_platform", "posts", ["platform"])
    op.create_index("ix_posts_published_at", "posts", ["published_at"])
    op.create_index("ix_channel_connections_platform", "channel_connections", ["platform"])
    op.create_index("ix_channel_connections_is_active", "channel_connections", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_channel_connections_is_active", table_name="channel_connections")
    op.drop_index("ix_channel_connections_platform", table_name="channel_connections")
    op.drop_index("ix_posts_published_at", table_name="posts")
    op.drop_index("ix_posts_platform", table_name="posts")
    op.drop_index("ix_posts_status", table_name="posts")

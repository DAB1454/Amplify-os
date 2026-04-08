"""add per-track streaming destination URLs

Adds spotify_url, apple_music_url, bandcamp_url to tracks. Used by
goal-aware CTA selection so a post about a single track can deep-link
straight to that track on the right DSP instead of the release-level
Linktree aggregator.

Revision ID: e2f3a4b5c678
Revises: d1e2f3a4b567
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa

revision = "e2f3a4b5c678"
down_revision = "d1e2f3a4b567"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tracks", sa.Column("spotify_url", sa.String(length=500), nullable=True))
    op.add_column("tracks", sa.Column("apple_music_url", sa.String(length=500), nullable=True))
    op.add_column("tracks", sa.Column("bandcamp_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("tracks", "bandcamp_url")
    op.drop_column("tracks", "apple_music_url")
    op.drop_column("tracks", "spotify_url")

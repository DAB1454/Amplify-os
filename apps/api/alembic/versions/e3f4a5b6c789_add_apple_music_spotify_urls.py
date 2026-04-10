"""add apple_music and spotify URLs to releases and artists

Artist-level page links and release-level album links for Apple Music
and Spotify, so the CTA system can auto-resolve destination URLs.

Revision ID: e3f4a5b6c789
Revises: d2e3f4a5b678
Create Date: 2026-04-10 14:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "e3f4a5b6c789"
down_revision = "d2e3f4a5b678"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Release-level album links
    op.add_column(
        "releases",
        sa.Column("apple_music_url", sa.String(500), nullable=True),
    )
    op.add_column(
        "releases",
        sa.Column("spotify_url", sa.String(500), nullable=True),
    )
    # Artist-level page links
    op.add_column(
        "artists",
        sa.Column("apple_music_url", sa.String(500), nullable=True),
    )
    op.add_column(
        "artists",
        sa.Column("spotify_artist_url", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("artists", "spotify_artist_url")
    op.drop_column("artists", "apple_music_url")
    op.drop_column("releases", "spotify_url")
    op.drop_column("releases", "apple_music_url")

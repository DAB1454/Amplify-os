"""add track_id to posts

Adds a hard FK from posts to tracks so a specific track row is the
post's anchor. Caption generation, audio selection, and asset
matching can now key off track_id instead of the fuzzy
track_reference string that has been driving content mismatches.

Backfill: for every existing post with a track_reference and a
campaign linked to a release, find the track in that release whose
title matches track_reference (case-insensitive, trimmed) and set
track_id. Posts where the match is ambiguous or absent are left
with track_id NULL — code that needs a track must handle that.

Revision ID: c8e9f0a1b234
Revises: a5b6c7d8e901
Create Date: 2026-05-19 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "c8e9f0a1b234"
down_revision = "a5b6c7d8e901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "posts",
        sa.Column(
            "track_id",
            sa.Uuid(),
            sa.ForeignKey("tracks.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_posts_track_id", "posts", ["track_id"])

    # Backfill from track_reference where unambiguous. Match on
    # case-insensitive, trimmed title within the post's release.
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            UPDATE posts AS p
            SET track_id = sub.track_id
            FROM (
                SELECT
                    posts.id AS post_id,
                    tracks.id AS track_id
                FROM posts
                JOIN campaigns ON campaigns.id = posts.campaign_id
                JOIN tracks
                    ON tracks.release_id = campaigns.release_id
                   AND tracks.tenant_id = posts.tenant_id
                   AND LOWER(TRIM(tracks.title)) = LOWER(TRIM(posts.track_reference))
                WHERE posts.track_reference IS NOT NULL
                  AND posts.track_reference <> ''
                  AND posts.track_id IS NULL
            ) AS sub
            WHERE p.id = sub.post_id
            """
        )
    else:
        # SQLite (local dev) — correlated subquery form
        op.execute(
            """
            UPDATE posts
            SET track_id = (
                SELECT tracks.id
                FROM tracks
                JOIN campaigns ON campaigns.id = posts.campaign_id
                WHERE tracks.release_id = campaigns.release_id
                  AND tracks.tenant_id = posts.tenant_id
                  AND LOWER(TRIM(tracks.title)) = LOWER(TRIM(posts.track_reference))
                LIMIT 1
            )
            WHERE posts.track_reference IS NOT NULL
              AND posts.track_reference <> ''
              AND posts.track_id IS NULL
            """
        )


def downgrade() -> None:
    op.drop_index("ix_posts_track_id", table_name="posts")
    op.drop_column("posts", "track_id")

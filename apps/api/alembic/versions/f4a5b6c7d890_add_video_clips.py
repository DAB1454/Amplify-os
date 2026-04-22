"""add video_clips, analysis_jobs tables, clip_id on posts, video metadata on assets

Revision ID: f4a5b6c7d890
Revises: e3f4a5b6c789
Create Date: 2026-04-22 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "f4a5b6c7d890"
down_revision = "e3f4a5b6c789"


def upgrade() -> None:
    # ── video_clips table ─���────────────────────────────────────────
    op.create_table(
        "video_clips",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("source_asset_id", sa.Uuid(), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("track_id", sa.Uuid(), sa.ForeignKey("tracks.id"), nullable=True),
        sa.Column("release_id", sa.Uuid(), sa.ForeignKey("releases.id"), nullable=True),
        sa.Column("artist_id", sa.Uuid(), sa.ForeignKey("artists.id"), nullable=True),
        # Clip boundaries
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        # Generated clip files
        sa.Column("clip_url_vertical", sa.String(500), nullable=True),
        sa.Column("clip_url_landscape", sa.String(500), nullable=True),
        sa.Column("clip_url_square", sa.String(500), nullable=True),
        sa.Column("thumbnail_url", sa.String(500), nullable=True),
        # AI analysis metadata
        sa.Column("clip_type", sa.String(30), server_default="unknown", nullable=False),
        sa.Column("energy_score", sa.Float(), server_default="0", nullable=False),
        sa.Column("scene_complexity", sa.Float(), server_default="0", nullable=False),
        sa.Column("ai_description", sa.Text(), server_default="", nullable=False),
        sa.Column("ai_tags", ARRAY(sa.String()), server_default="{}", nullable=False),
        # Usage tracking
        sa.Column("uses_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("avg_engagement_score", sa.Float(), nullable=True),
        sa.Column("test_status", sa.String(20), server_default="untested", nullable=False),
        # Status
        sa.Column("status", sa.String(20), server_default="processing", nullable=False),
        sa.Column("processing_error", sa.Text(), nullable=True),
        # Timestamps
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # ── video_clip_analysis_jobs table ──────��──────────────────────
    op.create_table(
        "video_clip_analysis_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("source_asset_id", sa.Uuid(), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("track_id", sa.Uuid(), sa.ForeignKey("tracks.id"), nullable=True),
        sa.Column("status", sa.String(20), server_default="queued", nullable=False),
        sa.Column("clips_detected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("clips_extracted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("analysis_config", sa.JSON(), server_default="{}"),
        sa.Column("analysis_results", sa.JSON(), server_default="{}"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # ── Add clip_id to posts ──────────���────────────────────────────
    op.add_column(
        "posts",
        sa.Column("clip_id", sa.Uuid(), sa.ForeignKey("video_clips.id", ondelete="SET NULL"), nullable=True),
    )

    # ── Add video metadata to assets ──────��────────────────────────
    op.add_column("assets", sa.Column("video_duration_seconds", sa.Float(), nullable=True))
    op.add_column("assets", sa.Column("video_width", sa.Integer(), nullable=True))
    op.add_column("assets", sa.Column("video_height", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("assets", "video_height")
    op.drop_column("assets", "video_width")
    op.drop_column("assets", "video_duration_seconds")
    op.drop_column("posts", "clip_id")
    op.drop_table("video_clip_analysis_jobs")
    op.drop_table("video_clips")

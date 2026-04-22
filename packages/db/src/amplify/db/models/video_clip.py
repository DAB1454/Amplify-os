"""Video clip ORM models — clips extracted from full music videos."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from amplify.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from amplify.db.models.asset import AssetModel
    from amplify.db.models.track import TrackModel
    from amplify.db.models.release import ReleaseModel


class VideoClipModel(Base, TimestampMixin):
    """A short clip extracted from a full music video.

    Each clip has pre-rendered versions in multiple aspect ratios
    (vertical 9:16, landscape 16:9, square 1:1) ready for posting.
    """

    __tablename__ = "video_clips"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    source_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assets.id"), nullable=False
    )
    track_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tracks.id"), nullable=True
    )
    release_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("releases.id"), nullable=True
    )
    artist_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artists.id"), nullable=True
    )

    # Clip boundaries in the source video
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)

    # Generated clip files (S3 URLs)
    clip_url_vertical: Mapped[str | None] = mapped_column(String(500), nullable=True)
    clip_url_landscape: Mapped[str | None] = mapped_column(String(500), nullable=True)
    clip_url_square: Mapped[str | None] = mapped_column(String(500), nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # AI analysis metadata
    clip_type: Mapped[str] = mapped_column(
        String(30), default="unknown", server_default="unknown"
    )
    # clip_type: chorus_drop, verse_opening, bridge, visual_peak, scene_change, energy_spike
    energy_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    scene_complexity: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    ai_description: Mapped[str] = mapped_column(Text, default="", server_default="")
    ai_tags: Mapped[list[str]] = mapped_column(
        ARRAY(String), default=list, server_default="{}"
    )

    # Usage tracking
    uses_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    avg_engagement_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    test_status: Mapped[str] = mapped_column(
        String(20), default="untested", server_default="untested", nullable=False
    )

    # Status
    status: Mapped[str] = mapped_column(
        String(20), default="processing", server_default="processing"
    )
    # status: processing, ready, failed, archived
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    source_asset: Mapped[AssetModel] = relationship(foreign_keys=[source_asset_id])
    track: Mapped[TrackModel | None] = relationship()
    release: Mapped[ReleaseModel | None] = relationship()


class VideoClipAnalysisJobModel(Base, TimestampMixin):
    """Tracks the async processing of a music video into clips."""

    __tablename__ = "video_clip_analysis_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    source_asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assets.id"), nullable=False
    )
    track_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tracks.id"), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(20), default="queued", server_default="queued"
    )
    # status: queued, analyzing, extracting, uploading, complete, failed
    clips_detected: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    clips_extracted: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )

    analysis_config: Mapped[dict] = mapped_column(JSON, default=dict)
    analysis_results: Mapped[dict] = mapped_column(JSON, default=dict)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

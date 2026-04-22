"""Asset and AssetVariant ORM models."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from amplify.db.base import Base, TimestampMixin, TenantMixin

if TYPE_CHECKING:
    from amplify.db.models.artist import ArtistModel
    from amplify.db.models.release import ReleaseModel
    from amplify.db.models.campaign import CampaignModel


class AssetModel(Base, TimestampMixin, TenantMixin):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    artist_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artists.id"), nullable=True
    )
    release_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("releases.id"), nullable=True
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaigns.id"), nullable=True
    )
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # asset_type: image, video, audio, album_art, promo_photo, logo, lyric_video
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    file_url: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")
    source: Mapped[str] = mapped_column(String(30), default="uploaded", server_default="uploaded")
    # source: uploaded, ai_generated, imported
    approval_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # approval_status: NULL (auto-approved), pending_review, approved, rejected
    generation_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    generation_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    # ── Learning: untested / proven lifecycle ──────────────────────
    # "untested" = never used in a published post that got metrics, or used
    #              too few times to trust. "proven" = used enough times to
    #              believe the average engagement is real. The planner
    #              prefers proven assets most of the time but occasionally
    #              schedules an untested asset as an experiment so the
    #              tenant's library keeps learning instead of stagnating
    #              around a handful of known winners.
    test_status: Mapped[str] = mapped_column(
        String(20), default="untested", server_default="untested", nullable=False
    )
    uses_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    avg_engagement_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Video metadata (populated for music_video asset_type)
    video_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    video_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    artist: Mapped[ArtistModel | None] = relationship()
    release: Mapped[ReleaseModel | None] = relationship(back_populates="assets")
    campaign: Mapped[CampaignModel | None] = relationship()
    variants: Mapped[list[AssetVariantModel]] = relationship(
        back_populates="asset", cascade="all, delete-orphan"
    )


class AssetVariantModel(Base):
    __tablename__ = "asset_variants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assets.id"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    file_url: Mapped[str] = mapped_column(String(500), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    format: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Relationships
    asset: Mapped[AssetModel] = relationship(back_populates="variants")

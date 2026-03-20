"""Release ORM model."""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from amplify.db.base import Base, TimestampMixin, TenantMixin

if TYPE_CHECKING:
    from amplify.db.models.artist import ArtistModel
    from amplify.db.models.track import TrackModel
    from amplify.db.models.asset import AssetModel


class ReleaseModel(Base, TimestampMixin, TenantMixin):
    __tablename__ = "releases"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    artist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artists.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    release_type: Mapped[str] = mapped_column(String(20), default="single")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    upc: Mapped[str | None] = mapped_column(String(20), nullable=True)
    artwork_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    # Destination URLs
    hyperfollow_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    linktree_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    bandcamp_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    youtube_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tiktok_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    instagram_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Relationships
    artist: Mapped[ArtistModel] = relationship(back_populates="releases")
    tracks: Mapped[list[TrackModel]] = relationship(
        back_populates="release", cascade="all, delete-orphan"
    )
    assets: Mapped[list[AssetModel]] = relationship(
        back_populates="release", cascade="all, delete-orphan"
    )

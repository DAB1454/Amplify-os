"""Artist ORM model."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from amplify.db.base import Base, TimestampMixin, TenantMixin

if TYPE_CHECKING:
    from amplify.db.models.tenant import TenantModel
    from amplify.db.models.release import ReleaseModel
    from amplify.db.models.campaign import CampaignModel


class ArtistModel(Base, TimestampMixin, TenantMixin):
    __tablename__ = "artists"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    bio: Mapped[str] = mapped_column(Text, default="")
    genre: Mapped[str] = mapped_column(String(100), default="")
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    spotify_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    social_links: Mapped[dict] = mapped_column(JSON, default=dict)

    # Relationships
    tenant: Mapped[TenantModel] = relationship(back_populates="artists")
    releases: Mapped[list[ReleaseModel]] = relationship(
        back_populates="artist", cascade="all, delete-orphan"
    )
    campaigns: Mapped[list[CampaignModel]] = relationship(
        back_populates="artist", cascade="all, delete-orphan"
    )

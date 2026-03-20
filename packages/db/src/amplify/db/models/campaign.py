"""Campaign ORM model."""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, Float, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from amplify.db.base import Base, TimestampMixin, TenantMixin

if TYPE_CHECKING:
    from amplify.db.models.artist import ArtistModel
    from amplify.db.models.release import ReleaseModel
    from amplify.db.models.post import PostModel
    from amplify.db.models.experiment import ExperimentModel


class CampaignModel(Base, TimestampMixin, TenantMixin):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    artist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artists.id"), nullable=False
    )
    release_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("releases.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    phase: Mapped[str] = mapped_column(String(30), default="pre_release")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    budget: Mapped[float | None] = mapped_column(Float, nullable=True)
    goals: Mapped[dict] = mapped_column(JSON, default=dict)
    config: Mapped[dict] = mapped_column(JSON, default=dict)

    # Relationships
    artist: Mapped[ArtistModel] = relationship(back_populates="campaigns")
    release: Mapped[ReleaseModel | None] = relationship()
    posts: Mapped[list[PostModel]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )
    experiments: Mapped[list[ExperimentModel]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )

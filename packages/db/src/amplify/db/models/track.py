"""Track ORM model."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from amplify.db.base import Base, TimestampMixin, TenantMixin

if TYPE_CHECKING:
    from amplify.db.models.release import ReleaseModel


class TrackModel(Base, TimestampMixin, TenantMixin):
    __tablename__ = "tracks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    release_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("releases.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    track_number: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    isrc: Mapped[str | None] = mapped_column(String(20), nullable=True)
    audio_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    lyrics: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_single: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relationships
    release: Mapped[ReleaseModel] = relationship(back_populates="tracks")

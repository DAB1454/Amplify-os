"""ChannelConnection ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from amplify.db.base import Base, TimestampMixin, TenantMixin

if TYPE_CHECKING:
    from amplify.db.models.artist import ArtistModel


class ChannelConnectionModel(Base, TimestampMixin, TenantMixin):
    __tablename__ = "channel_connections"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    artist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("artists.id"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    integration_mode: Mapped[str] = mapped_column(
        String(20), default="automatic", server_default="automatic"
    )
    platform_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)

    # OAuth metadata
    granted_scopes: Mapped[list] = mapped_column(JSON, default=list)
    account_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    token_encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    refresh_supported: Mapped[bool] = mapped_column(Boolean, default=True)
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_health_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    disconnect_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)

    # Relationships
    artist: Mapped[ArtistModel] = relationship()

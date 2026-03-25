"""Post ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from amplify.db.base import Base, TimestampMixin, TenantMixin

if TYPE_CHECKING:
    from amplify.db.models.campaign import CampaignModel
    from amplify.db.models.channel import ChannelConnectionModel


class PostModel(Base, TimestampMixin, TenantMixin):
    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaigns.id"), nullable=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channel_connections.id"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    content_text: Mapped[str] = mapped_column(Text, default="")
    media_urls: Mapped[list] = mapped_column(JSON, default=list)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    platform_post_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    permalink: Mapped[str | None] = mapped_column(String(500), nullable=True)
    engagement: Mapped[dict] = mapped_column(JSON, default=dict)
    destination_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    approval_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    day_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action_type_label: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Relationships
    campaign: Mapped[CampaignModel | None] = relationship(back_populates="posts")
    channel: Mapped[ChannelConnectionModel] = relationship()

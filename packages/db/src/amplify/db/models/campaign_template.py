"""Campaign template ORM model — clonable templates across tenants."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from amplify.db.base import Base, TimestampMixin


class CampaignTemplateModel(Base, TimestampMixin):
    __tablename__ = "campaign_templates"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    phase: Mapped[str] = mapped_column(String(30), default="pre_release")
    goals: Mapped[dict] = mapped_column(JSON, default=dict)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    post_templates: Mapped[list] = mapped_column(JSON, default=list)
    is_global: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    category: Mapped[str] = mapped_column(String(50), default="general")
    tags: Mapped[list] = mapped_column(JSON, default=list)

    __table_args__ = (
        Index("ix_campaign_templates_global", "is_global", "is_active"),
    )

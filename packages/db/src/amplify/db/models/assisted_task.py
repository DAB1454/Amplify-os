"""AssistedTask ORM model for manual-workflow integrations."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from amplify.db.base import Base, TimestampMixin, TenantMixin


class AssistedTaskModel(Base, TimestampMixin, TenantMixin):
    __tablename__ = "assisted_tasks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("channel_connections.id"), nullable=True
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaigns.id"), nullable=True
    )
    release_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("releases.id"), nullable=True
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    checklist: Mapped[dict] = mapped_column(JSON, default=list)
    url_validations: Mapped[dict] = mapped_column(JSON, default=list)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reminder_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    __table_args__ = (
        Index("ix_assisted_tasks_tenant_status", "tenant_id", "status"),
        Index("ix_assisted_tasks_tenant_platform", "tenant_id", "platform"),
    )

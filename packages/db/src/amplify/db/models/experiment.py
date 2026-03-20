"""Experiment ORM model."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from amplify.db.base import Base, TimestampMixin, TenantMixin

if TYPE_CHECKING:
    from amplify.db.models.campaign import CampaignModel


class ExperimentModel(Base, TimestampMixin, TenantMixin):
    __tablename__ = "experiments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")

    # Variables and variants
    variables: Mapped[list] = mapped_column(JSON, default=list)
    variants: Mapped[list] = mapped_column(JSON, default=list)

    # Cohort
    cohort_platform: Mapped[str] = mapped_column(String(50), default="")
    cohort_size: Mapped[int] = mapped_column(Integer, default=0)

    # Outcome
    winner_variant: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Config and dates
    metrics_config: Mapped[dict] = mapped_column(JSON, default=dict)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    campaign: Mapped[CampaignModel] = relationship(back_populates="experiments")

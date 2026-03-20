"""BillingPlan and Subscription ORM models."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from amplify.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from amplify.db.models.tenant import TenantModel


class BillingPlanModel(Base, TimestampMixin):
    __tablename__ = "billing_plans"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    tier: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    price_monthly: Mapped[float] = mapped_column(Float, default=0)
    price_yearly: Mapped[float] = mapped_column(Float, default=0)
    features: Mapped[list] = mapped_column(JSON, default=list)
    limits: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class SubscriptionModel(Base, TimestampMixin):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), unique=True, nullable=False
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("billing_plans.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    current_period_start: Mapped[date] = mapped_column(Date, nullable=False)
    current_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    tenant: Mapped[TenantModel] = relationship()
    plan: Mapped[BillingPlanModel] = relationship()

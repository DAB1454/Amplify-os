"""Tenant ORM model."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from amplify.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from amplify.db.models.membership import MembershipModel
    from amplify.db.models.artist import ArtistModel
    from amplify.db.models.release import ReleaseModel
    from amplify.db.models.campaign import CampaignModel


class TenantModel(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    plan_tier: Mapped[str] = mapped_column(String(20), default="solo")
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Automation graduation ladder. Each rung removes one user click from
    # the campaign loop:
    #   manual         — user generates and publishes everything by hand
    #   assisted       — AI generates plans, user approves each post
    #   auto_campaigns — AI generates AND auto-approves within guardrails
    #   autonomous     — AI decides when to plan + replans on its own
    # Defaults to manual so existing tenants are unaffected. The pause
    # flag is the kill switch — when true the worker tick skips the
    # tenant entirely regardless of automation_level.
    automation_level: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)
    automation_paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    memberships: Mapped[list[MembershipModel]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    artists: Mapped[list[ArtistModel]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )

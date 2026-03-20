"""Tenant domain models."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


PlanTier = Literal["solo", "pro", "agency"]


class Tenant(BaseModel):
    """A tenant (organization/label/artist account) in Amplify-OS."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    slug: str
    plan_tier: PlanTier = "solo"
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    onboarding_completed: bool = False
    settings: dict = Field(default_factory=dict)
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TenantCreate(BaseModel):
    """Schema for creating a new tenant."""

    name: str
    slug: str | None = None
    plan_tier: PlanTier = "solo"

    def generate_slug(self) -> str:
        """Auto-generate a slug from the tenant name."""
        if self.slug:
            return self.slug
        return re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")


class TenantUpdate(BaseModel):
    """Schema for updating an existing tenant."""

    name: str | None = None
    settings: dict | None = None
    plan_tier: PlanTier | None = None
    onboarding_completed: bool | None = None


class OnboardingStep(BaseModel):
    """A single step in the tenant onboarding wizard."""

    key: str
    label: str
    description: str = ""
    is_completed: bool = False
    is_required: bool = True


ONBOARDING_STEPS: list[OnboardingStep] = [
    OnboardingStep(
        key="create_artist",
        label="Add your first artist",
        description="Create an artist profile with name, bio, and genre.",
        is_required=True,
    ),
    OnboardingStep(
        key="connect_channel",
        label="Connect a channel",
        description="Link at least one platform (Instagram, TikTok, YouTube, etc).",
        is_required=True,
    ),
    OnboardingStep(
        key="add_release",
        label="Add a release",
        description="Create your first release with title, artwork, and links.",
        is_required=False,
    ),
    OnboardingStep(
        key="create_campaign",
        label="Create a campaign",
        description="Set up your first marketing campaign.",
        is_required=False,
    ),
    OnboardingStep(
        key="choose_plan",
        label="Choose a plan",
        description="Select the plan that fits your needs.",
        is_required=False,
    ),
]

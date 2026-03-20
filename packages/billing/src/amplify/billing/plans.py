"""Billing plan definitions for Amplify-OS.

Three tiers: solo (for individual artists), pro (labels/managers),
agency (multi-artist management firms).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


PlanTier = Literal["solo", "pro", "agency"]

ALL_TIERS: list[PlanTier] = ["solo", "pro", "agency"]


class PlanLimits(BaseModel):
    """Hard limits enforced per billing cycle."""

    artists: int = 1
    channels: int = 3
    scheduled_posts_per_month: int = 10
    active_campaigns: int = 2
    media_renders_per_month: int = 5
    ai_generations_per_month: int = 0
    team_members: int = 1
    campaign_templates: int = 0
    api_access: bool = False


class BillingPlan(BaseModel):
    """Represents a subscription plan."""

    id: str
    name: str
    tier: PlanTier
    price_monthly: float
    price_yearly: float
    features: list[str]
    limits: PlanLimits
    stripe_price_id_monthly: str = ""
    stripe_price_id_yearly: str = ""


DEFAULT_PLANS: list[BillingPlan] = [
    BillingPlan(
        id="solo",
        name="Solo",
        tier="solo",
        price_monthly=0,
        price_yearly=0,
        features=[
            "1 artist profile",
            "3 channels",
            "10 scheduled posts/month",
            "2 active campaigns",
            "5 media renders/month",
            "Basic templates",
        ],
        limits=PlanLimits(
            artists=1,
            channels=3,
            scheduled_posts_per_month=10,
            active_campaigns=2,
            media_renders_per_month=5,
            ai_generations_per_month=0,
            team_members=1,
            campaign_templates=0,
            api_access=False,
        ),
    ),
    BillingPlan(
        id="pro",
        name="Pro",
        tier="pro",
        price_monthly=29.00,
        price_yearly=290.00,
        features=[
            "5 artist profiles",
            "Unlimited channels",
            "200 scheduled posts/month",
            "10 active campaigns",
            "100 media renders/month",
            "AI content generation (200/mo)",
            "Campaign templates",
            "3 team members",
            "Priority support",
        ],
        limits=PlanLimits(
            artists=5,
            channels=-1,
            scheduled_posts_per_month=200,
            active_campaigns=10,
            media_renders_per_month=100,
            ai_generations_per_month=200,
            team_members=3,
            campaign_templates=20,
            api_access=False,
        ),
    ),
    BillingPlan(
        id="agency",
        name="Agency",
        tier="agency",
        price_monthly=99.00,
        price_yearly=990.00,
        features=[
            "Unlimited artist profiles",
            "Unlimited channels",
            "Unlimited scheduled posts",
            "Unlimited campaigns",
            "Unlimited media renders",
            "Full AI automation",
            "Unlimited campaign templates",
            "Unlimited team members",
            "API access",
            "Custom branding",
            "Dedicated support",
        ],
        limits=PlanLimits(
            artists=-1,
            channels=-1,
            scheduled_posts_per_month=-1,
            active_campaigns=-1,
            media_renders_per_month=-1,
            ai_generations_per_month=-1,
            team_members=-1,
            campaign_templates=-1,
            api_access=True,
        ),
    ),
]


def get_plan(tier: str) -> BillingPlan | None:
    """Look up a plan by tier name."""
    for plan in DEFAULT_PLANS:
        if plan.tier == tier:
            return plan
    return None


def get_plan_limits(tier: str) -> PlanLimits:
    """Return limits for a tier, defaulting to solo if unknown."""
    plan = get_plan(tier)
    if plan is None:
        plan = get_plan("solo")
    assert plan is not None
    return plan.limits

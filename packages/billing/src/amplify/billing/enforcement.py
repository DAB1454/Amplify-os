"""Subscription enforcement — checks entity counts and rate limits against plan.

This module is the single point of truth for "can this tenant do X?"
It checks both hard entity-count limits (artists, channels, campaigns)
and rate-based metering limits (posts, renders, AI gens).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .metering import METRIC_MEDIA_RENDERS, METRIC_POSTS_SCHEDULED, MeteringService
from .plans import PlanLimits, get_plan_limits


@dataclass
class EnforcementResult:
    """Result of a limit check."""

    allowed: bool
    metric: str
    current: int
    limit: int
    tier: str
    message: str = ""

    @property
    def at_limit(self) -> bool:
        return self.limit != -1 and self.current >= self.limit


class SubscriptionEnforcer:
    """Checks entity counts and rate limits against tenant plan limits.

    Entity counts are read from the database (artists, channels, campaigns).
    Rate limits are read from the MeteringService (posts, renders, AI gens).
    """

    def __init__(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        plan_tier: str,
        metering: MeteringService | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.plan_tier = plan_tier
        self.limits = get_plan_limits(plan_tier)
        self.metering = metering or MeteringService()

    async def _count_entities(self, model_class: type) -> int:
        """Count entities for this tenant."""
        result = await self.db.execute(
            select(func.count())
            .select_from(model_class)
            .where(model_class.tenant_id == self.tenant_id)
        )
        return result.scalar_one()

    async def _count_active_campaigns(self) -> int:
        from amplify.db.models.campaign import CampaignModel

        result = await self.db.execute(
            select(func.count())
            .select_from(CampaignModel)
            .where(
                CampaignModel.tenant_id == self.tenant_id,
                CampaignModel.status.in_(["active", "draft"]),
            )
        )
        return result.scalar_one()

    def _check(self, metric: str, current: int, limit: int) -> EnforcementResult:
        if limit == -1:
            return EnforcementResult(
                allowed=True, metric=metric, current=current,
                limit=limit, tier=self.plan_tier,
            )
        allowed = current < limit
        msg = "" if allowed else (
            f"{metric} limit reached: {current}/{limit} on {self.plan_tier} plan. "
            "Upgrade to increase your limit."
        )
        return EnforcementResult(
            allowed=allowed, metric=metric, current=current,
            limit=limit, tier=self.plan_tier, message=msg,
        )

    # ── Entity-count checks ──────────────────────────────────────

    async def can_add_artist(self) -> EnforcementResult:
        from amplify.db.models.artist import ArtistModel

        count = await self._count_entities(ArtistModel)
        return self._check("artists", count, self.limits.artists)

    async def can_add_channel(self) -> EnforcementResult:
        from amplify.db.models.channel import ChannelConnectionModel

        count = await self._count_entities(ChannelConnectionModel)
        return self._check("channels", count, self.limits.channels)

    async def can_add_campaign(self) -> EnforcementResult:
        count = await self._count_active_campaigns()
        return self._check("active_campaigns", count, self.limits.active_campaigns)

    async def can_add_team_member(self) -> EnforcementResult:
        from amplify.db.models.membership import MembershipModel

        result = await self.db.execute(
            select(func.count())
            .select_from(MembershipModel)
            .where(MembershipModel.tenant_id == self.tenant_id)
        )
        count = result.scalar_one()
        return self._check("team_members", count, self.limits.team_members)

    # ── Rate-based checks ────────────────────────────────────────

    async def can_schedule_post(self) -> EnforcementResult:
        tid = str(self.tenant_id)
        current = await self.metering.get_usage(tid, METRIC_POSTS_SCHEDULED)
        return self._check(
            "scheduled_posts_per_month", current,
            self.limits.scheduled_posts_per_month,
        )

    async def can_render_media(self) -> EnforcementResult:
        tid = str(self.tenant_id)
        current = await self.metering.get_usage(tid, METRIC_MEDIA_RENDERS)
        return self._check(
            "media_renders_per_month", current,
            self.limits.media_renders_per_month,
        )

    # ── Dashboard summary ────────────────────────────────────────

    async def get_usage_summary(self) -> dict:
        """Return a summary of current usage vs limits for the dashboard."""
        from amplify.db.models.artist import ArtistModel
        from amplify.db.models.channel import ChannelConnectionModel

        tid = str(self.tenant_id)
        artists = await self._count_entities(ArtistModel)
        channels = await self._count_entities(ChannelConnectionModel)
        campaigns = await self._count_active_campaigns()
        posts = await self.metering.get_usage(tid, METRIC_POSTS_SCHEDULED)
        renders = await self.metering.get_usage(tid, METRIC_MEDIA_RENDERS)

        def _item(label: str, current: int, limit: int) -> dict:
            return {
                "label": label,
                "current": current,
                "limit": limit,
                "unlimited": limit == -1,
                "at_limit": limit != -1 and current >= limit,
                "pct": 0 if limit <= 0 else min(round(current / limit * 100), 100),
            }

        return {
            "tier": self.plan_tier,
            "items": [
                _item("Artists", artists, self.limits.artists),
                _item("Channels", channels, self.limits.channels),
                _item("Active Campaigns", campaigns, self.limits.active_campaigns),
                _item("Scheduled Posts", posts, self.limits.scheduled_posts_per_month),
                _item("Media Renders", renders, self.limits.media_renders_per_month),
            ],
        }

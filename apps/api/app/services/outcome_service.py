"""Outcome and reward computation service.

Persists raw outcome metrics in PostOutcome, computes phase-aware reward
scores with full breakdown, and supports per-tenant weight overrides.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.campaign import CampaignModel
from amplify.db.models.learning import PostFeatureVectorModel, PostOutcomeModel
from amplify.db.models.post import PostModel
from amplify.db.models.tenant import TenantModel
from amplify.learning.rewards.calculator import (
    OutcomeData,
    RewardCalculator,
    RewardWeights,
)

logger = logging.getLogger(__name__)


class OutcomeService:
    """Records post outcomes, computes rewards, and persists both."""

    def __init__(self, db: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self._calculator = RewardCalculator()

    async def record_outcome(
        self,
        post_id: uuid.UUID,
        measurement_window: str,
        metrics: dict[str, Any],
        measured_at: datetime | None = None,
        source: str = "",
    ) -> PostOutcomeModel:
        """Record an outcome measurement and compute its reward.

        If an outcome already exists for this post+window, it is updated.
        The reward is computed using the phase from the post's feature vector
        or campaign, with per-tenant weight overrides applied.
        """
        if measured_at is None:
            measured_at = datetime.now(timezone.utc).replace(tzinfo=None)

        # Load context for phase-aware weighting
        phase = await self._resolve_phase(post_id)
        weight_overrides = await self._load_tenant_weight_overrides()
        published_at = await self._get_published_at(post_id)

        # Build outcome data and compute rates
        raw = self._build_outcome_data(metrics)

        # Compute reward
        breakdown = self._calculator.compute(
            raw,
            observed_at=published_at,
            phase=phase,
            weight_overrides=weight_overrides,
            measurement_window=measurement_window,
        )

        # Compute derived rates for persistence
        base = metrics.get("impressions") or metrics.get("reach") or 0
        engagement_rate = None
        save_rate = None
        click_through_rate = None
        if base > 0:
            if metrics.get("engagements") is not None:
                engagement_rate = metrics["engagements"] / base
            if metrics.get("saves") is not None:
                save_rate = metrics["saves"] / base
            if metrics.get("clicks") is not None:
                click_through_rate = metrics["clicks"] / base

        # Upsert outcome row
        outcome = await self._upsert_outcome(
            post_id=post_id,
            measurement_window=measurement_window,
            metrics=metrics,
            engagement_rate=engagement_rate,
            save_rate=save_rate,
            click_through_rate=click_through_rate,
            reward=breakdown.final_reward,
            reward_breakdown=breakdown.to_dict(),
            measured_at=measured_at,
            source=source,
        )

        return outcome

    async def get_outcomes(
        self,
        post_id: uuid.UUID,
    ) -> list[PostOutcomeModel]:
        """Get all outcome measurements for a post."""
        stmt = (
            select(PostOutcomeModel)
            .where(
                PostOutcomeModel.post_id == post_id,
                PostOutcomeModel.tenant_id == self.tenant_id,
            )
            .order_by(PostOutcomeModel.measured_at)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_outcome(
        self,
        post_id: uuid.UUID,
    ) -> PostOutcomeModel | None:
        """Get the most recent outcome measurement for a post."""
        stmt = (
            select(PostOutcomeModel)
            .where(
                PostOutcomeModel.post_id == post_id,
                PostOutcomeModel.tenant_id == self.tenant_id,
            )
            .order_by(PostOutcomeModel.measured_at.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def recompute_rewards(
        self,
        post_id: uuid.UUID,
        weight_overrides: dict[str, float] | None = None,
    ) -> list[PostOutcomeModel]:
        """Recompute rewards for all outcome windows of a post.

        Useful when weight overrides change or for A/B testing different
        reward formulas.
        """
        outcomes = await self.get_outcomes(post_id)
        if not outcomes:
            return []

        phase = await self._resolve_phase(post_id)
        overrides = weight_overrides or await self._load_tenant_weight_overrides()
        published_at = await self._get_published_at(post_id)

        for outcome in outcomes:
            raw = self._outcome_model_to_data(outcome)
            breakdown = self._calculator.compute(
                raw,
                observed_at=published_at,
                phase=phase,
                weight_overrides=overrides,
                measurement_window=outcome.measurement_window,
            )
            outcome.reward = breakdown.final_reward
            outcome.reward_breakdown = breakdown.to_dict()

        await self.db.flush()
        for outcome in outcomes:
            await self.db.refresh(outcome)
        return outcomes

    # ── Private helpers ───────────────────────────────────────────

    async def _resolve_phase(self, post_id: uuid.UUID) -> str | None:
        """Determine the campaign/release phase for a post."""
        # Try feature vector first (has computed release_phase)
        stmt = select(PostFeatureVectorModel).where(
            PostFeatureVectorModel.post_id == post_id,
            PostFeatureVectorModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        vector = result.scalar_one_or_none()
        if vector and vector.features:
            phase = vector.features.get("release_phase") or vector.campaign_phase
            if phase:
                return phase

        # Fallback to campaign.phase
        stmt = (
            select(CampaignModel.phase)
            .join(PostModel, PostModel.campaign_id == CampaignModel.id)
            .where(
                PostModel.id == post_id,
                PostModel.tenant_id == self.tenant_id,
            )
        )
        result = await self.db.execute(stmt)
        row = result.first()
        return row[0] if row else None

    async def _load_tenant_weight_overrides(self) -> dict[str, float] | None:
        """Load per-tenant reward weight overrides from tenant settings."""
        stmt = select(TenantModel.settings).where(TenantModel.id == self.tenant_id)
        result = await self.db.execute(stmt)
        row = result.first()
        if not row or not row[0]:
            return None
        settings = row[0]
        return settings.get("reward_weights")

    async def _get_published_at(self, post_id: uuid.UUID) -> datetime | None:
        """Get the publish timestamp for temporal decay."""
        stmt = select(PostModel.published_at).where(
            PostModel.id == post_id,
            PostModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        row = result.first()
        return row[0] if row else None

    async def _upsert_outcome(
        self,
        post_id: uuid.UUID,
        measurement_window: str,
        metrics: dict[str, Any],
        engagement_rate: float | None,
        save_rate: float | None,
        click_through_rate: float | None,
        reward: float,
        reward_breakdown: dict,
        measured_at: datetime,
        source: str,
    ) -> PostOutcomeModel:
        """Create or update an outcome row."""
        stmt = select(PostOutcomeModel).where(
            PostOutcomeModel.post_id == post_id,
            PostOutcomeModel.measurement_window == measurement_window,
            PostOutcomeModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            existing.impressions = metrics.get("impressions")
            existing.reach = metrics.get("reach")
            existing.engagements = metrics.get("engagements")
            existing.saves = metrics.get("saves")
            existing.shares = metrics.get("shares")
            existing.clicks = metrics.get("clicks")
            existing.comments_count = metrics.get("comments") or metrics.get("comments_count")
            existing.followers_gained = metrics.get("followers_gained")
            existing.engagement_rate = engagement_rate
            existing.save_rate = save_rate
            existing.click_through_rate = click_through_rate
            existing.reward = reward
            existing.reward_breakdown = reward_breakdown
            existing.measured_at = measured_at
            existing.source = source
            await self.db.flush()
            return existing

        outcome = PostOutcomeModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            post_id=post_id,
            measurement_window=measurement_window,
            impressions=metrics.get("impressions"),
            reach=metrics.get("reach"),
            engagements=metrics.get("engagements"),
            saves=metrics.get("saves"),
            shares=metrics.get("shares"),
            clicks=metrics.get("clicks"),
            comments_count=metrics.get("comments") or metrics.get("comments_count"),
            followers_gained=metrics.get("followers_gained"),
            engagement_rate=engagement_rate,
            save_rate=save_rate,
            click_through_rate=click_through_rate,
            reward=reward,
            reward_breakdown=reward_breakdown,
            measured_at=measured_at,
            source=source,
        )
        self.db.add(outcome)
        await self.db.flush()
        await self.db.refresh(outcome)
        return outcome

    @staticmethod
    def _build_outcome_data(metrics: dict[str, Any]) -> OutcomeData:
        """Convert a raw metrics dict into OutcomeData."""
        return OutcomeData(
            impressions=metrics.get("impressions"),
            reach=metrics.get("reach"),
            engagements=metrics.get("engagements"),
            saves=metrics.get("saves"),
            shares=metrics.get("shares"),
            clicks=metrics.get("clicks"),
            comments=metrics.get("comments") or metrics.get("comments_count"),
            followers_gained=metrics.get("followers_gained"),
            engagement_rate=metrics.get("engagement_rate"),
            save_rate=metrics.get("save_rate"),
            share_rate=metrics.get("share_rate"),
            comment_rate=metrics.get("comment_rate"),
            click_through_rate=metrics.get("click_through_rate"),
            follow_rate=metrics.get("follow_rate"),
            watch_time_seconds=metrics.get("watch_time_seconds"),
            completion_rate=metrics.get("completion_rate"),
            email_captures=metrics.get("email_captures"),
            email_capture_rate=metrics.get("email_capture_rate"),
            conversions=metrics.get("conversions"),
            conversion_rate=metrics.get("conversion_rate"),
        )

    @staticmethod
    def _outcome_model_to_data(outcome: PostOutcomeModel) -> OutcomeData:
        """Convert a PostOutcomeModel back to OutcomeData for recomputation."""
        return OutcomeData(
            impressions=outcome.impressions,
            reach=outcome.reach,
            engagements=outcome.engagements,
            saves=outcome.saves,
            shares=outcome.shares,
            clicks=outcome.clicks,
            comments=outcome.comments_count,
            followers_gained=outcome.followers_gained,
            engagement_rate=outcome.engagement_rate,
            save_rate=outcome.save_rate,
            click_through_rate=outcome.click_through_rate,
        )

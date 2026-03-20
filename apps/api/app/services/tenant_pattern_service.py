"""Tenant pattern learning service.

Aggregates PostFeatureVector + PostOutcome data into TenantPattern records.
Designed to be called nightly (or on-demand) per tenant.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.learning import (
    PostFeatureVectorModel,
    PostOutcomeModel,
    TenantPatternModel,
)
from amplify.learning.patterns.engine import (
    PatternCandidate,
    PatternConfig,
    PatternEngine,
    Recommendation,
)

logger = logging.getLogger(__name__)

# Best measurement window to use for reward (prefer longer windows)
_WINDOW_PRIORITY = ["7d", "final", "48h", "24h", "3h", "1h"]


class TenantPatternService:
    """Learns and persists tenant-specific patterns from feature-outcome data."""

    def __init__(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        config: PatternConfig | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.config = config or PatternConfig()

    async def update_patterns(
        self,
        now: datetime | None = None,
    ) -> list[TenantPatternModel]:
        """Run the nightly pattern learning job for this tenant.

        1. Load all feature vectors + their best outcomes
        2. Feed into PatternEngine
        3. Upsert TenantPattern rows (skip pinned ones)
        4. Deactivate stale patterns that are no longer supported
        """
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)

        # Load feature-outcome pairs
        rows = await self._load_feature_outcome_pairs()
        if not rows:
            logger.info("tenant=%s: no feature-outcome data, skipping", self.tenant_id)
            return []

        # Build engine and ingest all data
        engine = PatternEngine(self.config)
        for features, reward, observed_at in rows:
            engine.ingest(features, reward, observed_at, now=now)

        if engine.not_enough_data():
            logger.info(
                "tenant=%s: not enough data (%d obs), skipping",
                self.tenant_id, engine._total_count,
            )
            return []

        # Discover patterns
        candidates = engine.discover_patterns()

        # Load existing patterns to check pinned status
        existing = await self._load_active_patterns()
        pinned_keys = {
            (p.subject_feature, p.subject_value)
            for p in existing
            if p.is_pinned
        }

        # Upsert patterns
        result: list[TenantPatternModel] = []
        seen_keys: set[tuple[str | None, str | None]] = set()

        for cand in candidates:
            key = (cand.feature, cand.value)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            # Skip pinned patterns — don't overwrite admin preferences
            if key in pinned_keys:
                pinned = next(
                    p for p in existing
                    if (p.subject_feature, p.subject_value) == key and p.is_pinned
                )
                result.append(pinned)
                continue

            pattern = await self._upsert_pattern(cand, now)
            result.append(pattern)

        # Deactivate patterns no longer supported by data (but not pinned)
        await self._deactivate_stale(seen_keys, pinned_keys)

        await self.db.flush()
        return result

    async def get_patterns(
        self,
        pattern_type: str | None = None,
        direction: str | None = None,
        include_inactive: bool = False,
    ) -> list[TenantPatternModel]:
        """Retrieve tenant patterns with filtering."""
        stmt = (
            select(TenantPatternModel)
            .where(TenantPatternModel.tenant_id == self.tenant_id)
            .order_by(TenantPatternModel.confidence.desc())
        )
        if not include_inactive:
            stmt = stmt.where(TenantPatternModel.is_active == True)  # noqa: E712
        if pattern_type:
            stmt = stmt.where(TenantPatternModel.pattern_type == pattern_type)
        if direction:
            stmt = stmt.where(TenantPatternModel.direction == direction)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_recommendations(
        self,
        now: datetime | None = None,
    ) -> list[dict]:
        """Generate recommendations from stored patterns + live data.

        Returns a list of recommendation dicts with evidence and confidence.
        If not enough data, returns an empty list with a status message.
        """
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)

        rows = await self._load_feature_outcome_pairs()
        if not rows:
            return []

        engine = PatternEngine(self.config)
        for features, reward, observed_at in rows:
            engine.ingest(features, reward, observed_at, now=now)

        if engine.not_enough_data():
            return []

        # Collect pinned preferences
        pinned = await self._load_pinned_preferences()

        recs = engine.generate_recommendations(pinned_features=pinned)
        return [
            {
                "feature": r.feature,
                "recommended_value": r.recommended_value,
                "title": r.title,
                "explanation": r.explanation,
                "confidence": r.confidence,
                "evidence": r.evidence,
                "pattern_type": r.pattern_type,
            }
            for r in recs
        ]

    async def pin_pattern(
        self,
        pattern_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> TenantPatternModel:
        """Pin a pattern so it won't be overwritten by nightly learning."""
        stmt = select(TenantPatternModel).where(
            TenantPatternModel.id == pattern_id,
            TenantPatternModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        pattern = result.scalar_one_or_none()
        if pattern is None:
            raise ValueError(f"Pattern {pattern_id} not found")

        pattern.is_pinned = True
        pattern.pinned_by = user_id
        pattern.pinned_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self.db.flush()
        await self.db.refresh(pattern)
        return pattern

    async def unpin_pattern(
        self,
        pattern_id: uuid.UUID,
    ) -> TenantPatternModel:
        """Unpin a pattern so nightly jobs can update it again."""
        stmt = select(TenantPatternModel).where(
            TenantPatternModel.id == pattern_id,
            TenantPatternModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        pattern = result.scalar_one_or_none()
        if pattern is None:
            raise ValueError(f"Pattern {pattern_id} not found")

        pattern.is_pinned = False
        pattern.pinned_by = None
        pattern.pinned_at = None
        await self.db.flush()
        await self.db.refresh(pattern)
        return pattern

    async def get_data_status(self) -> dict:
        """Return summary of available data for this tenant."""
        vector_count = await self.db.scalar(
            select(func.count(PostFeatureVectorModel.id)).where(
                PostFeatureVectorModel.tenant_id == self.tenant_id
            )
        )
        outcome_count = await self.db.scalar(
            select(func.count(PostOutcomeModel.id)).where(
                PostOutcomeModel.tenant_id == self.tenant_id
            )
        )
        pattern_count = await self.db.scalar(
            select(func.count(TenantPatternModel.id)).where(
                TenantPatternModel.tenant_id == self.tenant_id,
                TenantPatternModel.is_active == True,  # noqa: E712
            )
        )
        return {
            "feature_vectors": vector_count or 0,
            "outcomes": outcome_count or 0,
            "active_patterns": pattern_count or 0,
            "min_required": self.config.min_sample_count,
            "has_enough_data": (vector_count or 0) >= self.config.min_sample_count,
        }

    # ── Private helpers ───────────────────────────────────────────

    async def _load_feature_outcome_pairs(
        self,
    ) -> list[tuple[dict, float, datetime]]:
        """Load feature vectors joined with their best outcome reward."""
        # Get all feature vectors for this tenant
        stmt = (
            select(PostFeatureVectorModel)
            .where(PostFeatureVectorModel.tenant_id == self.tenant_id)
        )
        result = await self.db.execute(stmt)
        vectors = list(result.scalars().all())

        if not vectors:
            return []

        # Get outcomes for all these posts, grouped by post_id
        post_ids = [v.post_id for v in vectors]
        outcome_stmt = (
            select(PostOutcomeModel)
            .where(
                PostOutcomeModel.tenant_id == self.tenant_id,
                PostOutcomeModel.post_id.in_(post_ids),
                PostOutcomeModel.reward.is_not(None),
            )
        )
        outcome_result = await self.db.execute(outcome_stmt)
        outcomes = list(outcome_result.scalars().all())

        # Pick the best (longest-window) outcome per post
        best_outcome: dict[uuid.UUID, PostOutcomeModel] = {}
        for o in outcomes:
            existing = best_outcome.get(o.post_id)
            if existing is None:
                best_outcome[o.post_id] = o
            else:
                if self._window_rank(o.measurement_window) > self._window_rank(
                    existing.measurement_window
                ):
                    best_outcome[o.post_id] = o

        # Join: feature vector + reward
        rows = []
        for vec in vectors:
            outcome = best_outcome.get(vec.post_id)
            if outcome is None or outcome.reward is None:
                continue
            rows.append((vec.features, outcome.reward, vec.extracted_at))

        return rows

    @staticmethod
    def _window_rank(window: str) -> int:
        """Higher rank = more mature measurement window."""
        try:
            return _WINDOW_PRIORITY.index(window)
        except ValueError:
            return -1

    async def _load_active_patterns(self) -> list[TenantPatternModel]:
        """Load all active patterns for this tenant."""
        stmt = (
            select(TenantPatternModel)
            .where(
                TenantPatternModel.tenant_id == self.tenant_id,
                TenantPatternModel.is_active == True,  # noqa: E712
            )
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def _load_pinned_preferences(self) -> dict[str, str]:
        """Load pinned (feature → value) pairs."""
        stmt = (
            select(TenantPatternModel)
            .where(
                TenantPatternModel.tenant_id == self.tenant_id,
                TenantPatternModel.is_pinned == True,  # noqa: E712
                TenantPatternModel.is_active == True,  # noqa: E712
            )
        )
        result = await self.db.execute(stmt)
        patterns = list(result.scalars().all())
        return {
            p.subject_feature: p.subject_value
            for p in patterns
            if p.subject_feature and p.subject_value
        }

    async def _upsert_pattern(
        self,
        cand: PatternCandidate,
        now: datetime,
    ) -> TenantPatternModel:
        """Create or update a pattern from a candidate."""
        stmt = select(TenantPatternModel).where(
            TenantPatternModel.tenant_id == self.tenant_id,
            TenantPatternModel.subject_feature == cand.feature,
            TenantPatternModel.subject_value == cand.value,
            TenantPatternModel.is_active == True,  # noqa: E712
        )
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()

        pattern_type = PatternEngine._feature_to_pattern_type(cand.feature)

        if existing:
            existing.direction = cand.direction
            existing.title = cand.title
            existing.explanation = cand.explanation
            existing.confidence = cand.confidence
            existing.supporting_observation_count = cand.raw_count
            existing.decay_weighted_reward = cand.mean_reward
            existing.sample_count_decayed = cand.decayed_count
            existing.evidence = cand.evidence
            existing.observation_window_start = cand.observation_window_start
            existing.observation_window_end = cand.observation_window_end
            existing.pattern_type = pattern_type
            existing.version += 1
            await self.db.flush()
            await self.db.refresh(existing)
            return existing

        pattern = TenantPatternModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            pattern_type=pattern_type,
            subject_feature=cand.feature,
            subject_value=cand.value,
            direction=cand.direction,
            title=cand.title,
            explanation=cand.explanation,
            confidence=cand.confidence,
            supporting_observation_count=cand.raw_count,
            decay_weighted_reward=cand.mean_reward,
            sample_count_decayed=cand.decayed_count,
            evidence=cand.evidence,
            observation_window_start=cand.observation_window_start,
            observation_window_end=cand.observation_window_end,
            source="tenant",
            is_active=True,
            version=1,
        )
        self.db.add(pattern)
        await self.db.flush()
        await self.db.refresh(pattern)
        return pattern

    async def _deactivate_stale(
        self,
        seen_keys: set[tuple[str | None, str | None]],
        pinned_keys: set[tuple[str | None, str | None]],
    ) -> None:
        """Deactivate patterns that are no longer supported by data."""
        active = await self._load_active_patterns()
        for p in active:
            key = (p.subject_feature, p.subject_value)
            if key not in seen_keys and key not in pinned_keys:
                p.is_active = False

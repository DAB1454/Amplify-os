"""DB-backed global prior service.

Bridges the pure-logic GlobalPriorService with SQLAlchemy models.
Persists GlobalPatternStat records and provides cold-start
recommendations for new tenants.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.learning.config import LearningConfig
from amplify.learning.global_priors.service import (
    PATTERN_CATEGORIES,
    GlobalPriorService as Engine,
    GlobalRecommendation,
)
from amplify.learning.schemas.observation import Observation


class DBGlobalPriorService:
    """Manages global pattern stats and cold-start recommendations."""

    def __init__(
        self,
        db: AsyncSession,
        config: LearningConfig | None = None,
    ) -> None:
        self.db = db
        self.config = config or LearningConfig()
        self.engine = Engine(self.config)

    # ── Compute & persist ─────────────────────────────────────────

    async def compute_and_store(
        self,
        observations: list[Observation],
        action_type: str = "",
        source_cohort_id: uuid.UUID | None = None,
        source_cohort_name: str = "",
    ) -> list[dict[str, Any]]:
        """Aggregate observations, persist as GlobalPatternStat records.

        Returns the list of newly stored pattern stats.
        """
        all_recs = self.engine.aggregate_all_categories(
            observations,
            action_type=action_type,
            source_cohort_id=source_cohort_id,
            source_cohort_name=source_cohort_name,
        )

        stored: list[dict[str, Any]] = []
        for category, recs in all_recs.items():
            for rec in recs:
                model = await self._upsert_pattern_stat(rec, category, source_cohort_id)
                stored.append(self._model_to_dict(model))

        await self.db.flush()
        return stored

    # ── Query ─────────────────────────────────────────────────────

    async def list_active_priors(
        self,
        category: str | None = None,
        source_cohort_id: uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        """List active global pattern stats, optionally filtered."""
        from amplify.db.models.learning import GlobalPatternStatModel

        stmt = select(GlobalPatternStatModel).where(
            GlobalPatternStatModel.is_active == True,  # noqa: E712
        )
        if category:
            stmt = stmt.where(GlobalPatternStatModel.category == category)
        if source_cohort_id:
            stmt = stmt.where(
                GlobalPatternStatModel.source_cohort_id == source_cohort_id,
            )
        stmt = stmt.order_by(GlobalPatternStatModel.mean_reward.desc())

        result = await self.db.execute(stmt)
        return [self._model_to_dict(m) for m in result.scalars().all()]

    async def get_cold_start_recommendations(
        self,
        tenant_observation_count: int,
        category: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Get recommendations for a cold-start tenant from stored priors.

        Returns only patterns that pass threshold gating and are labeled
        as global priors.
        """
        from amplify.db.models.learning import GlobalPatternStatModel

        cold_start_threshold = self.config.ranking.cold_start_threshold

        if tenant_observation_count >= cold_start_threshold:
            return {}

        stmt = select(GlobalPatternStatModel).where(
            GlobalPatternStatModel.is_active == True,  # noqa: E712
            GlobalPatternStatModel.tenant_count >= self.config.privacy.k_anonymity_threshold,
        )
        if category:
            stmt = stmt.where(GlobalPatternStatModel.category == category)

        stmt = stmt.order_by(GlobalPatternStatModel.mean_reward.desc())
        result = await self.db.execute(stmt)
        rows = result.scalars().all()

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            cat = row.category or "uncategorized"
            rec = self._model_to_dict(row)
            rec["source"] = "global_prior"
            rec["explanation"] = (
                f"Based on aggregate data from {row.tenant_count} accounts. "
                f"This is a global prior, not learned from your specific data."
            )
            grouped.setdefault(cat, [])
            grouped[cat].append(rec)

        return grouped

    # ── Internal ──────────────────────────────────────────────────

    async def _upsert_pattern_stat(
        self,
        rec: GlobalRecommendation,
        category: str,
        source_cohort_id: uuid.UUID | None,
    ):
        """Insert or update a GlobalPatternStatModel."""
        from amplify.db.models.learning import GlobalPatternStatModel

        # Try to find existing
        stmt = select(GlobalPatternStatModel).where(
            GlobalPatternStatModel.feature_name == rec.feature_name,
            GlobalPatternStatModel.feature_value == rec.feature_value,
            GlobalPatternStatModel.action_type == "",
        )
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()

        now = datetime.now(timezone.utc)

        if existing:
            existing.mean_reward = rec.mean_reward
            existing.std_dev = rec.std_dev
            existing.confidence_lower = rec.confidence_lower
            existing.confidence_upper = rec.confidence_upper
            existing.observation_count = rec.observation_count
            existing.tenant_count = rec.tenant_count
            existing.source_cohort_id = source_cohort_id
            existing.category = category
            existing.computed_at = now
            existing.version = existing.version + 1
            existing.is_active = True
            return existing
        else:
            model = GlobalPatternStatModel(
                id=uuid.uuid4(),
                feature_name=rec.feature_name,
                feature_value=rec.feature_value,
                action_type="",
                mean_reward=rec.mean_reward,
                std_dev=rec.std_dev,
                confidence_lower=rec.confidence_lower,
                confidence_upper=rec.confidence_upper,
                observation_count=rec.observation_count,
                tenant_count=rec.tenant_count,
                k_anonymity_threshold=self.config.privacy.k_anonymity_threshold,
                source_cohort_id=source_cohort_id,
                category=category,
                computed_at=now,
            )
            self.db.add(model)
            return model

    @staticmethod
    def _model_to_dict(model) -> dict[str, Any]:
        created_at = getattr(model, "created_at", None)
        updated_at = getattr(model, "updated_at", None)
        return {
            "id": str(model.id),
            "feature_name": model.feature_name,
            "feature_value": model.feature_value,
            "action_type": model.action_type,
            "mean_reward": model.mean_reward,
            "std_dev": model.std_dev,
            "confidence_lower": model.confidence_lower,
            "confidence_upper": model.confidence_upper,
            "observation_count": model.observation_count,
            "tenant_count": model.tenant_count,
            "k_anonymity_threshold": model.k_anonymity_threshold,
            "source_cohort_id": (
                str(model.source_cohort_id) if model.source_cohort_id else None
            ),
            "category": model.category,
            "computed_at": model.computed_at.isoformat() if model.computed_at else None,
            "version": model.version,
            "is_active": model.is_active if model.is_active is not None else True,
            "created_at": created_at.isoformat() if created_at else None,
            "updated_at": updated_at.isoformat() if updated_at else None,
        }

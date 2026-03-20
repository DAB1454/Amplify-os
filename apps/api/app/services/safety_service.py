"""Service layer for exploration controls and safety rails.

Bridges the SafetyRails engine with the database — loads tenant
settings, persists rollback decisions, tracks experiment counts,
and emits audit log entries for every safety action.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.learning import (
    EvaluationRunModel,
    LearningAuditLogModel,
    LearningEventModel,
)
from amplify.db.models.tenant import TenantModel
from amplify.learning.safety.rails import (
    Alert,
    ExplorationControls,
    RollbackDecision,
    SafetyCheckResult,
    SafetyRails,
)


class SafetyService:
    """DB-backed safety enforcement service."""

    def __init__(self, db: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self._rails = SafetyRails()

    # ── Controls CRUD ──────────────────────────────────────────

    async def get_controls(self) -> ExplorationControls:
        """Load the tenant's exploration controls from the settings JSON."""
        tenant = await self._get_tenant()
        settings = tenant.settings or {}
        return ExplorationControls.from_dict(settings.get("exploration", {}))

    async def update_controls(
        self,
        updates: dict[str, Any],
        user_id: uuid.UUID | None = None,
    ) -> ExplorationControls:
        """Update exploration controls (partial update)."""
        tenant = await self._get_tenant()
        settings = dict(tenant.settings or {})
        exploration = dict(settings.get("exploration", {}))
        exploration.update(updates)

        # Validate before persisting
        controls = ExplorationControls.from_dict(exploration)
        errors = controls.validate()
        if errors:
            raise ValueError(f"Invalid exploration settings: {'; '.join(errors)}")

        settings["exploration"] = controls.to_dict()
        tenant.settings = settings
        await self.db.flush()

        await self._audit("exploration_controls.updated", "tenant", self.tenant_id, {
            "updates": updates,
            "new_settings": controls.to_dict(),
            "updated_by": str(user_id) if user_id else None,
        })

        return controls

    # ── Safety checks ──────────────────────────────────────────

    async def check_before_exploration(
        self,
        *,
        platform: str = "",
        requested_epsilon: float = 0.1,
        total_observations: int | None = None,
        rank_correlation: float | None = None,
        pending_approval: bool = False,
    ) -> SafetyCheckResult:
        """Run a full safety check before an exploration action."""
        controls = await self.get_controls()

        # Compute experiment counts from DB
        week_start = datetime.now(timezone.utc) - timedelta(days=7)
        experiments_this_week = await self._count_experiments(since=week_start)
        platform_experiments = await self._count_experiments(
            since=week_start, platform=platform,
        ) if platform else 0

        # Get total observations if not provided
        if total_observations is None:
            total_observations = await self._count_total_observations()

        current_hour = datetime.now(timezone.utc).hour

        result = self._rails.check_exploration(
            controls,
            current_hour_utc=current_hour,
            experiments_this_week=experiments_this_week,
            platform=platform,
            platform_experiments_this_week=platform_experiments,
            total_observations=total_observations,
            rank_correlation=rank_correlation,
            requested_epsilon=requested_epsilon,
            pending_approval=pending_approval,
        )

        # Audit the check
        await self._audit("safety_check.performed", "tenant", self.tenant_id, {
            "allowed": result.allowed,
            "reason": result.reason,
            "force_shadow": result.force_shadow,
            "force_fallback": result.force_fallback,
            "capped_epsilon": result.capped_epsilon,
            "platform": platform,
            "alert_codes": [a.code for a in result.alerts],
        })

        return result

    async def evaluate_rollback(self) -> RollbackDecision:
        """Check whether the tenant should auto-rollback based on recent performance."""
        controls = await self.get_controls()

        baseline_rewards = await self._get_baseline_rewards(controls.baseline_window)
        recent_rewards = await self._get_recent_rewards(controls.baseline_window)

        decision = self._rails.evaluate_rollback(
            controls,
            baseline_rewards=baseline_rewards,
            recent_rewards=recent_rewards,
        )

        if decision.should_rollback:
            await self._audit("auto_rollback.triggered", "tenant", self.tenant_id, {
                "baseline_reward": decision.baseline_reward,
                "current_reward": decision.current_reward,
                "drop_fraction": decision.drop_fraction,
                "threshold": decision.threshold,
            })

        return decision

    async def detect_anomalies(self) -> list[Alert]:
        """Run anomaly detection for the tenant."""
        controls = await self.get_controls()
        week_start = datetime.now(timezone.utc) - timedelta(days=7)
        experiments_this_week = await self._count_experiments(since=week_start)

        alerts = self._rails.detect_anomalies(
            controls,
            experiments_this_week=experiments_this_week,
        )

        if any(a.severity.value == "critical" for a in alerts):
            await self._audit("anomaly.critical", "tenant", self.tenant_id, {
                "alerts": [a.to_dict() for a in alerts],
            })

        return alerts

    async def get_experiment_stats(self) -> dict[str, Any]:
        """Return current experiment usage stats for the dashboard."""
        controls = await self.get_controls()
        week_start = datetime.now(timezone.utc) - timedelta(days=7)
        experiments_this_week = await self._count_experiments(since=week_start)
        total_observations = await self._count_total_observations()

        platform_usage: dict[str, int] = {}
        for platform in controls.channel_limits:
            platform_usage[platform] = await self._count_experiments(
                since=week_start, platform=platform,
            )

        return {
            "controls": controls.to_dict(),
            "experiments_this_week": experiments_this_week,
            "total_observations": total_observations,
            "platform_usage": platform_usage,
            "budget_remaining": max(
                0, controls.max_experiments_per_week - experiments_this_week
            ),
        }

    # ── DB queries ─────────────────────────────────────────────

    async def _get_tenant(self) -> TenantModel:
        stmt = select(TenantModel).where(TenantModel.id == self.tenant_id)
        result = await self.db.execute(stmt)
        tenant = result.scalar_one_or_none()
        if not tenant:
            raise ValueError(f"Tenant {self.tenant_id} not found")
        return tenant

    async def _count_experiments(
        self,
        since: datetime,
        platform: str | None = None,
    ) -> int:
        """Count exploratory learning events since a given time."""
        stmt = select(func.count(LearningEventModel.id)).where(
            LearningEventModel.tenant_id == self.tenant_id,
            LearningEventModel.action_type == "bandit_exploration",
            LearningEventModel.observed_at >= since,
        )
        if platform:
            stmt = stmt.where(LearningEventModel.platform == platform)
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    async def _count_total_observations(self) -> int:
        """Count total learning events for this tenant."""
        stmt = select(func.count(LearningEventModel.id)).where(
            LearningEventModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    async def _get_baseline_rewards(self, window: int) -> list[float]:
        """Get the last N non-exploratory rewards."""
        stmt = (
            select(LearningEventModel.reward)
            .where(
                LearningEventModel.tenant_id == self.tenant_id,
                LearningEventModel.reward.is_not(None),
                LearningEventModel.action_type != "bandit_exploration",
            )
            .order_by(LearningEventModel.observed_at.desc())
            .limit(window)
        )
        result = await self.db.execute(stmt)
        return [r[0] for r in result.all() if r[0] is not None]

    async def _get_recent_rewards(self, window: int) -> list[float]:
        """Get the last N rewards (all types)."""
        stmt = (
            select(LearningEventModel.reward)
            .where(
                LearningEventModel.tenant_id == self.tenant_id,
                LearningEventModel.reward.is_not(None),
            )
            .order_by(LearningEventModel.observed_at.desc())
            .limit(window)
        )
        result = await self.db.execute(stmt)
        return [r[0] for r in result.all() if r[0] is not None]

    async def _audit(
        self,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID,
        details: dict,
    ) -> None:
        row = LearningAuditLogModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            schema_version=1,
        )
        self.db.add(row)

"""Usage metering service for Amplify-OS billing.

Tracks per-tenant consumption of billable metrics and enforces plan limits.
Checks both count-based limits (from DB) and rate-based limits (from metering).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone

from .plans import PlanLimits, get_plan_limits

# Billable metric names
METRIC_POSTS_SCHEDULED = "posts_scheduled"
METRIC_AI_GENERATIONS = "ai_generations"
METRIC_MEDIA_RENDERS = "media_renders"
METRIC_API_CALLS = "api_calls"

ALL_METRICS = (
    METRIC_POSTS_SCHEDULED,
    METRIC_AI_GENERATIONS,
    METRIC_MEDIA_RENDERS,
    METRIC_API_CALLS,
)

# Maps billable metrics to PlanLimits field names
_METRIC_TO_LIMIT_FIELD: dict[str, str] = {
    METRIC_POSTS_SCHEDULED: "scheduled_posts_per_month",
    METRIC_AI_GENERATIONS: "ai_generations_per_month",
    METRIC_MEDIA_RENDERS: "media_renders_per_month",
}


class LimitExceeded(Exception):
    """Raised when a tenant exceeds a plan limit."""

    def __init__(self, metric: str, current: int, limit: int, tier: str) -> None:
        self.metric = metric
        self.current = current
        self.limit = limit
        self.tier = tier
        super().__init__(
            f"{metric} limit exceeded: {current}/{limit} on {tier} plan"
        )


class MeteringService:
    """In-memory metering service.

    In production this would be backed by a time-series store or a
    dedicated metering database table.
    """

    def __init__(self) -> None:
        # {(tenant_id, metric, period): quantity}
        self._usage: dict[tuple[str, str, str], int] = defaultdict(int)

    @staticmethod
    def _current_period() -> str:
        """Return the current billing period as YYYY-MM."""
        return datetime.now(timezone.utc).strftime("%Y-%m")

    async def record_usage(
        self,
        tenant_id: str,
        metric: str,
        quantity: int = 1,
    ) -> None:
        """Record usage of a billable metric."""
        period = self._current_period()
        self._usage[(tenant_id, metric, period)] += quantity

    async def get_usage(
        self,
        tenant_id: str,
        metric: str,
        period: str | None = None,
    ) -> int:
        """Return total usage for a metric in the given period."""
        if period is None:
            period = self._current_period()
        return self._usage.get((tenant_id, metric, period), 0)

    async def get_all_usage(
        self,
        tenant_id: str,
        period: str | None = None,
    ) -> dict[str, int]:
        """Return all metric usage for a tenant in the given period."""
        if period is None:
            period = self._current_period()
        return {
            metric: self._usage.get((tenant_id, metric, period), 0)
            for metric in ALL_METRICS
        }

    async def check_limit(
        self,
        tenant_id: str,
        metric: str,
        plan_tier: str,
    ) -> bool:
        """Check whether the tenant is within their plan limit for a metric.

        Returns True if usage is within the limit, False if exceeded.
        """
        limits = get_plan_limits(plan_tier)
        limit_field = _METRIC_TO_LIMIT_FIELD.get(metric)
        if limit_field is None:
            return True

        limit = getattr(limits, limit_field, 0)
        if limit == -1:
            return True

        current = await self.get_usage(tenant_id, metric)
        return current < limit

    async def enforce_limit(
        self,
        tenant_id: str,
        metric: str,
        plan_tier: str,
    ) -> None:
        """Raise LimitExceeded if the tenant has hit their plan limit."""
        limits = get_plan_limits(plan_tier)
        limit_field = _METRIC_TO_LIMIT_FIELD.get(metric)
        if limit_field is None:
            return

        limit = getattr(limits, limit_field, 0)
        if limit == -1:
            return

        current = await self.get_usage(tenant_id, metric)
        if current >= limit:
            raise LimitExceeded(metric, current, limit, plan_tier)

    def reset_usage(self, tenant_id: str, period: str | None = None) -> None:
        """Reset all usage for a tenant in a period. For testing."""
        if period is None:
            period = self._current_period()
        for metric in ALL_METRICS:
            self._usage.pop((tenant_id, metric, period), None)

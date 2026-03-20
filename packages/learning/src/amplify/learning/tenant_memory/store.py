"""Tenant memory store — in-memory implementation.

This is the initial implementation using in-memory storage.  A future
iteration will add a database-backed store (via amplify-db) that persists
across restarts.  The interface is designed so that swapping storage
backends requires no changes to callers.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from amplify.learning.config import MemoryConfig
from amplify.learning.schemas.observation import Observation
from amplify.learning.schemas.insight import Insight


class TenantMemoryStore:
    """Stores observations and insights per tenant.

    Thread-safety: this initial implementation is NOT thread-safe.
    In production, access will be serialized by the async task queue.
    """

    def __init__(self, config: MemoryConfig | None = None) -> None:
        self.config = config or MemoryConfig()
        self._observations: dict[uuid.UUID, list[Observation]] = defaultdict(list)
        self._insights: dict[uuid.UUID, list[Insight]] = defaultdict(list)

    # ── Observations ───────────────────────────────────────────────

    def record(self, observation: Observation) -> None:
        """Record a new observation for a tenant."""
        tenant_obs = self._observations[observation.tenant_id]
        tenant_obs.append(observation)

        # Evict oldest if over capacity
        if len(tenant_obs) > self.config.max_observations_per_tenant:
            tenant_obs.sort(key=lambda o: o.observed_at)
            excess = len(tenant_obs) - self.config.max_observations_per_tenant
            del tenant_obs[:excess]

    def get_observations(
        self,
        tenant_id: uuid.UUID,
        *,
        action_type: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[Observation]:
        """Retrieve observations for a tenant with optional filters."""
        obs = self._observations.get(tenant_id, [])
        if action_type:
            obs = [o for o in obs if o.action_type == action_type]
        if since:
            obs = [o for o in obs if o.observed_at >= since]
        obs.sort(key=lambda o: o.observed_at, reverse=True)
        if limit:
            obs = obs[:limit]
        return obs

    def observation_count(self, tenant_id: uuid.UUID) -> int:
        return len(self._observations.get(tenant_id, []))

    # ── Insights ───────────────────────────────────────────────────

    def store_insight(self, insight: Insight) -> None:
        """Store a derived insight for a tenant."""
        tenant_insights = self._insights[insight.tenant_id]
        tenant_insights.append(insight)

        # Evict oldest if over capacity
        if len(tenant_insights) > self.config.max_insights_per_tenant:
            tenant_insights.sort(key=lambda i: i.created_at)
            excess = len(tenant_insights) - self.config.max_insights_per_tenant
            del tenant_insights[:excess]

    def get_insights(
        self,
        tenant_id: uuid.UUID,
        *,
        insight_type: str | None = None,
    ) -> list[Insight]:
        """Retrieve insights for a tenant."""
        insights = self._insights.get(tenant_id, [])
        if insight_type:
            insights = [i for i in insights if i.insight_type == insight_type]
        return sorted(insights, key=lambda i: i.created_at, reverse=True)

    # ── Compaction ─────────────────────────────────────────────────

    def compact(self, tenant_id: uuid.UUID, now: datetime | None = None) -> int:
        """Remove observations older than the compaction window.

        Returns the number of observations removed.
        """
        now = now or datetime.utcnow()
        cutoff = now - timedelta(days=self.config.compaction_age_days)
        before = len(self._observations.get(tenant_id, []))
        self._observations[tenant_id] = [
            o for o in self._observations.get(tenant_id, [])
            if o.observed_at >= cutoff
        ]
        return before - len(self._observations[tenant_id])

    # ── Diagnostics ────────────────────────────────────────────────

    def stats(self, tenant_id: uuid.UUID) -> dict:
        """Return diagnostic stats for a tenant's memory."""
        obs = self._observations.get(tenant_id, [])
        return {
            "observation_count": len(obs),
            "insight_count": len(self._insights.get(tenant_id, [])),
            "oldest_observation": min((o.observed_at for o in obs), default=None),
            "newest_observation": max((o.observed_at for o in obs), default=None),
        }

"""Global prior — a single aggregated statistic across tenants."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class GlobalPrior(BaseModel):
    """An aggregated statistic derived from cross-tenant observations.

    Always includes provenance: how many tenants and observations
    contributed, and when it was computed.
    """

    # What this prior is about
    feature_name: str  # e.g. "post_hour"
    feature_value: str  # e.g. "18"
    action_type: str = ""  # e.g. "publish_post"

    # The aggregate statistic
    mean_reward: float = 0.0
    observation_count: int = 0
    tenant_count: int = 0  # how many distinct tenants contributed

    # Confidence interval
    std_dev: float | None = None
    confidence_lower: float | None = None
    confidence_upper: float | None = None

    # Provenance
    computed_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def is_reliable(self) -> bool:
        """Whether this prior meets minimum evidence thresholds."""
        return self.observation_count >= 10 and self.tenant_count >= 2

    model_config = {"frozen": True}

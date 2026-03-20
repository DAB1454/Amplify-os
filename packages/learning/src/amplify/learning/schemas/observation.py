"""Observation — a single recorded action-outcome pair.

An observation captures *what happened* when an action was taken:
the context (features), the action itself, and the measured outcomes.
This is the fundamental unit of data the learning system operates on.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class OutcomeMetrics(BaseModel):
    """Measured outcomes for an observation.

    All metrics are optional — not every platform reports every signal.
    Missing metrics are excluded from reward computation rather than
    treated as zero.
    """

    impressions: int | None = None
    reach: int | None = None
    engagements: int | None = None
    saves: int | None = None
    shares: int | None = None
    clicks: int | None = None
    comments: int | None = None
    followers_gained: int | None = None

    # Derived rates (computed at observation time, not downstream)
    engagement_rate: float | None = None
    save_rate: float | None = None
    click_through_rate: float | None = None


class Observation(BaseModel):
    """A single action-outcome pair recorded by the learning system.

    Observations are immutable once created.  They flow from
    feature_extraction → rewards → tenant_memory and optionally
    (after privacy filtering) into global_priors.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    tenant_id: uuid.UUID
    artist_id: uuid.UUID | None = None

    # What action was taken
    action_type: str  # e.g. "publish_post", "schedule_post", "boost_post"
    action_id: uuid.UUID | None = None  # FK to the post/campaign/etc.

    # Context at the time of the action
    features: dict[str, Any] = Field(default_factory=dict)

    # What happened
    outcomes: OutcomeMetrics = Field(default_factory=OutcomeMetrics)

    # Composite reward score (computed by rewards module)
    reward: float | None = None

    # When
    observed_at: datetime = Field(default_factory=datetime.utcnow)
    outcome_measured_at: datetime | None = None

    # Provenance
    source: str = ""  # e.g. "instagram_insights", "youtube_analytics"
    confidence: float = 1.0  # 0-1, how reliable this observation is

    model_config = {"frozen": True}

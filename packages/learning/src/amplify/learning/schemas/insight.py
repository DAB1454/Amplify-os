"""Insight — a human-readable, auditable learning conclusion.

Insights are the output of the learning system that humans interact with.
They explain *what the system learned* and *why*, grounded in observations.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class InsightType(str, Enum):
    """Category of insight."""

    TIMING = "timing"  # best times to post
    CONTENT = "content"  # what content types perform
    PLATFORM = "platform"  # platform-specific patterns
    AUDIENCE = "audience"  # audience behavior patterns
    CAMPAIGN = "campaign"  # campaign strategy patterns
    ANOMALY = "anomaly"  # unexpected deviations


class Insight(BaseModel):
    """A single learning insight, always grounded in evidence.

    Every insight links back to the observations that support it and
    includes a confidence score and human-readable explanation.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    tenant_id: uuid.UUID
    insight_type: InsightType
    title: str  # short, human-readable summary
    explanation: str  # detailed explanation with evidence
    confidence: float = Field(ge=0.0, le=1.0)  # statistical confidence

    # What feature/action this insight is about
    subject_feature: str | None = None  # e.g. "post_hour", "content_type"
    subject_value: str | None = None  # e.g. "18", "carousel"

    # Evidence
    supporting_observation_count: int = 0
    observation_window_start: datetime | None = None
    observation_window_end: datetime | None = None

    # Whether this insight comes from tenant data or global priors
    source: str = "tenant"  # "tenant" or "global_prior"

    # Lifecycle
    created_at: datetime = Field(default_factory=datetime.utcnow)
    superseded_by: uuid.UUID | None = None  # if a newer insight replaces this

    model_config = {"frozen": True}

"""Score schemas — ranking outputs with mandatory explanations.

Every score produced by the ranking system includes a full breakdown
of *why* that score was assigned, enabling human review and debugging.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class ScoreComponent(BaseModel):
    """One contributing factor to a final score."""

    name: str  # e.g. "timing_match", "content_type_prior"
    weight: float  # how much this component contributed
    raw_value: float  # the component's raw score before weighting
    weighted_value: float  # weight * raw_value
    explanation: str  # human-readable reason


class RankingExplanation(BaseModel):
    """Full breakdown of how a ranking score was computed."""

    components: list[ScoreComponent] = Field(default_factory=list)
    tenant_weight: float = 0.0  # how much came from tenant-specific data
    prior_weight: float = 0.0  # how much came from global priors
    is_exploratory: bool = False  # was this boosted for exploration
    observation_count: int = 0  # how many observations informed this

    @property
    def summary(self) -> str:
        """One-line summary of the ranking rationale."""
        parts = []
        top = sorted(self.components, key=lambda c: abs(c.weighted_value), reverse=True)
        for c in top[:3]:
            parts.append(c.explanation)
        source = "tenant data" if self.tenant_weight > self.prior_weight else "global patterns"
        return f"Score based on {source}: {'; '.join(parts)}"


class ScoredAction(BaseModel):
    """A candidate action with its computed score and explanation."""

    action_id: uuid.UUID | None = None
    action_type: str = ""
    action_params: dict[str, Any] = Field(default_factory=dict)

    score: float = 0.0
    rank: int = 0  # 1-based position after sorting
    explanation: RankingExplanation = Field(default_factory=RankingExplanation)

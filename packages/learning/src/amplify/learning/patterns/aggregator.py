"""Feature-outcome aggregator with exponential decay.

Takes a stream of (feature_dict, reward, observed_at) rows and produces
per-feature-value aggregates with decay-weighted statistics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class AggregateRow:
    """Decay-weighted statistics for one (feature, value) pair."""

    feature: str
    value: str
    raw_count: int = 0
    decayed_count: float = 0.0
    decayed_reward_sum: float = 0.0
    reward_sum_sq: float = 0.0  # for std dev
    min_reward: float = float("inf")
    max_reward: float = float("-inf")
    oldest_at: datetime | None = None
    newest_at: datetime | None = None

    @property
    def mean_reward(self) -> float:
        if self.decayed_count < 1e-9:
            return 0.0
        return self.decayed_reward_sum / self.decayed_count

    @property
    def std_dev(self) -> float:
        if self.raw_count < 2:
            return 0.0
        variance = (self.reward_sum_sq / self.raw_count) - (
            (self.decayed_reward_sum / max(self.decayed_count, 1e-9)) ** 2
        )
        return math.sqrt(max(variance, 0.0))


class FeatureAggregator:
    """Aggregates feature-reward observations with exponential time decay.

    Parameters
    ----------
    half_life_days : float
        Half-life for exponential decay (default 30 days).
    tracked_features : set[str] | None
        If provided, only aggregate these feature names. None = aggregate all.
    """

    def __init__(
        self,
        half_life_days: float = 30.0,
        tracked_features: set[str] | None = None,
    ) -> None:
        self.half_life_days = half_life_days
        self.tracked_features = tracked_features
        self._aggregates: dict[tuple[str, str], AggregateRow] = {}

    def add(
        self,
        features: dict[str, Any],
        reward: float,
        observed_at: datetime,
        now: datetime | None = None,
    ) -> None:
        """Add one observation to the aggregator."""
        now = now or datetime.utcnow()
        decay = self._decay_weight(observed_at, now)

        for feat_name, feat_val in features.items():
            if feat_val is None:
                continue
            if self.tracked_features and feat_name not in self.tracked_features:
                continue
            # Skip internal/metadata features
            if feat_name.startswith("_"):
                continue

            val_str = str(feat_val)
            key = (feat_name, val_str)

            if key not in self._aggregates:
                self._aggregates[key] = AggregateRow(feature=feat_name, value=val_str)

            agg = self._aggregates[key]
            agg.raw_count += 1
            agg.decayed_count += decay
            agg.decayed_reward_sum += reward * decay
            agg.reward_sum_sq += reward ** 2
            agg.min_reward = min(agg.min_reward, reward)
            agg.max_reward = max(agg.max_reward, reward)

            if agg.oldest_at is None or observed_at < agg.oldest_at:
                agg.oldest_at = observed_at
            if agg.newest_at is None or observed_at > agg.newest_at:
                agg.newest_at = observed_at

    def get_aggregates(self, feature: str | None = None) -> list[AggregateRow]:
        """Return all aggregate rows, optionally filtered by feature name."""
        rows = list(self._aggregates.values())
        if feature:
            rows = [r for r in rows if r.feature == feature]
        return sorted(rows, key=lambda r: r.mean_reward, reverse=True)

    def get(self, feature: str, value: str) -> AggregateRow | None:
        """Get aggregate for a specific (feature, value) pair."""
        return self._aggregates.get((feature, value))

    def _decay_weight(self, observed_at: datetime, now: datetime) -> float:
        """Exponential decay weight: 1.0 for now, 0.5 at half_life_days ago."""
        age_days = max((now - observed_at).total_seconds() / 86400.0, 0.0)
        if self.half_life_days <= 0:
            return 1.0
        return math.exp(-0.693147 * age_days / self.half_life_days)

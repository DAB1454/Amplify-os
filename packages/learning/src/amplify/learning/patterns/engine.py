"""Pattern learning engine — converts aggregated feature-outcome data into
explainable tenant patterns and recommendations.

Pure logic layer — no DB access.  The service layer feeds data in and
persists results.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from amplify.learning.patterns.aggregator import AggregateRow, FeatureAggregator


# ── Configuration ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class PatternConfig:
    """Thresholds and behavior for pattern detection."""

    # Minimum raw observations before a pattern is surfaced
    min_sample_count: int = 5

    # Minimum decayed sample weight
    min_decayed_count: float = 2.0

    # Minimum confidence score to surface a pattern
    min_confidence: float = 0.3

    # Features to track for pattern learning
    tracked_features: set[str] = field(default_factory=lambda: {
        "content_hook_family",
        "content_cta_type",
        "post_hour",
        "timing_day_part",
        "post_day_of_week",
        "content_caption_bucket",
        "asset_type",
        "asset_aspect_ratio",
        "asset_video_duration_bucket",
        "channel_platform",
        "campaign_release_phase",
        "campaign_objective",
    })

    # Decay half-life in days
    decay_half_life_days: float = 30.0

    # How far below average to flag as "losing"
    losing_threshold_factor: float = 0.7

    # How far above average to flag as "winning"
    winning_threshold_factor: float = 1.3

    # Maximum patterns to return per feature
    max_patterns_per_feature: int = 5

    # Maximum recommendations to return
    max_recommendations: int = 20


# ── Human-readable labels ────────────────────────────────────────────

_FEATURE_LABELS: dict[str, str] = {
    "content_hook_family": "hook style",
    "content_cta_type": "call-to-action",
    "post_hour": "posting hour",
    "timing_day_part": "time of day",
    "post_day_of_week": "day of week",
    "content_caption_bucket": "caption length",
    "asset_type": "media format",
    "asset_aspect_ratio": "aspect ratio",
    "asset_video_duration_bucket": "video length",
    "channel_platform": "platform",
    "campaign_release_phase": "release phase",
    "campaign_objective": "campaign goal",
}

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _human_value(feature: str, value: str) -> str:
    """Convert a feature value to a human-readable string."""
    if feature == "post_day_of_week":
        try:
            return _DAY_NAMES[int(value)]
        except (ValueError, IndexError):
            return value
    if feature == "post_hour":
        try:
            h = int(value)
            suffix = "AM" if h < 12 else "PM"
            display = h % 12 or 12
            return f"{display}:00 {suffix}"
        except ValueError:
            return value
    return value.replace("_", " ")


# ── Output types ─────────────────────────────────────────────────────


@dataclass
class PatternCandidate:
    """A discovered pattern ready for persistence."""

    feature: str
    value: str
    direction: str  # "winning" or "losing"
    mean_reward: float
    baseline_reward: float  # feature-level average for comparison
    raw_count: int
    decayed_count: float
    confidence: float
    title: str
    explanation: str
    evidence: dict[str, Any]
    observation_window_start: datetime | None
    observation_window_end: datetime | None


@dataclass
class Recommendation:
    """An actionable recommendation derived from patterns."""

    feature: str
    recommended_value: str
    title: str
    explanation: str
    confidence: float
    evidence: dict[str, Any]
    pattern_type: str  # maps to TenantPatternModel.pattern_type


# ── Engine ───────────────────────────────────────────────────────────


class PatternEngine:
    """Discovers patterns and generates recommendations from feature-outcome data.

    Usage:
        engine = PatternEngine(config)
        engine.ingest(features_dict, reward, observed_at)
        ...repeat for all posts...
        patterns = engine.discover_patterns()
        recommendations = engine.generate_recommendations()
    """

    def __init__(self, config: PatternConfig | None = None) -> None:
        self.config = config or PatternConfig()
        self._aggregator = FeatureAggregator(
            half_life_days=self.config.decay_half_life_days,
            tracked_features=self.config.tracked_features,
        )
        self._total_reward_sum = 0.0
        self._total_count = 0

    def ingest(
        self,
        features: dict[str, Any],
        reward: float,
        observed_at: datetime,
        now: datetime | None = None,
    ) -> None:
        """Feed one observation into the engine."""
        self._aggregator.add(features, reward, observed_at, now=now)
        self._total_reward_sum += reward
        self._total_count += 1

    @property
    def global_mean_reward(self) -> float:
        if self._total_count == 0:
            return 0.0
        return self._total_reward_sum / self._total_count

    def discover_patterns(self) -> list[PatternCandidate]:
        """Analyze all aggregated data and return discovered patterns."""
        if self._total_count == 0:
            return []

        global_mean = self.global_mean_reward
        all_patterns: list[PatternCandidate] = []

        # Group aggregates by feature
        aggregates = self._aggregator.get_aggregates()
        by_feature: dict[str, list[AggregateRow]] = {}
        for agg in aggregates:
            by_feature.setdefault(agg.feature, []).append(agg)

        for feature, rows in by_feature.items():
            # Compute per-feature baseline
            total_decayed = sum(r.decayed_count for r in rows)
            if total_decayed < 1e-9:
                continue
            feature_mean = sum(r.decayed_reward_sum for r in rows) / total_decayed

            for row in rows:
                if row.raw_count < self.config.min_sample_count:
                    continue
                if row.decayed_count < self.config.min_decayed_count:
                    continue

                confidence = self._compute_confidence(row, len(rows))
                if confidence < self.config.min_confidence:
                    continue

                direction = self._classify_direction(
                    row.mean_reward, feature_mean
                )
                if direction is None:
                    continue  # too close to baseline

                title, explanation = self._generate_explanation(
                    feature, row, direction, feature_mean
                )

                all_patterns.append(PatternCandidate(
                    feature=feature,
                    value=row.value,
                    direction=direction,
                    mean_reward=round(row.mean_reward, 6),
                    baseline_reward=round(feature_mean, 6),
                    raw_count=row.raw_count,
                    decayed_count=round(row.decayed_count, 4),
                    confidence=round(confidence, 4),
                    title=title,
                    explanation=explanation,
                    evidence={
                        "raw_count": row.raw_count,
                        "decayed_count": round(row.decayed_count, 4),
                        "mean_reward": round(row.mean_reward, 6),
                        "baseline_reward": round(feature_mean, 6),
                        "global_mean_reward": round(global_mean, 6),
                        "std_dev": round(row.std_dev, 6),
                        "min_reward": round(row.min_reward, 6),
                        "max_reward": round(row.max_reward, 6),
                        "lift_vs_baseline": round(
                            (row.mean_reward / feature_mean - 1.0) * 100
                            if feature_mean > 1e-9 else 0.0, 2
                        ),
                    },
                    observation_window_start=row.oldest_at,
                    observation_window_end=row.newest_at,
                ))

        # Sort: winning first (highest confidence), then losing
        all_patterns.sort(
            key=lambda p: (0 if p.direction == "winning" else 1, -p.confidence)
        )
        return all_patterns

    def generate_recommendations(
        self,
        pinned_features: dict[str, str] | None = None,
    ) -> list[Recommendation]:
        """Generate actionable recommendations from discovered patterns.

        Parameters
        ----------
        pinned_features : dict[str, str] | None
            Feature→value pairs that the admin has pinned. Pinned features
            are returned as-is with boosted confidence rather than being
            overridden by data.
        """
        pinned_features = pinned_features or {}
        patterns = self.discover_patterns()
        recommendations: list[Recommendation] = []
        seen_features: set[str] = set()

        # First: include pinned preferences
        for feat, val in pinned_features.items():
            feat_label = _FEATURE_LABELS.get(feat, feat.replace("_", " "))
            human_val = _human_value(feat, val)
            recommendations.append(Recommendation(
                feature=feat,
                recommended_value=val,
                title=f"Use {human_val} ({feat_label}) — pinned by admin",
                explanation=(
                    f"This preference for {human_val} was pinned by an admin "
                    f"and will not be changed by automated learning."
                ),
                confidence=1.0,
                evidence={"pinned": True, "pinned_value": val},
                pattern_type=self._feature_to_pattern_type(feat),
            ))
            seen_features.add(feat)

        # Then: top winning pattern per feature
        for pat in patterns:
            if pat.feature in seen_features:
                continue
            if pat.direction != "winning":
                continue
            feat_label = _FEATURE_LABELS.get(pat.feature, pat.feature.replace("_", " "))
            human_val = _human_value(pat.feature, pat.value)
            recommendations.append(Recommendation(
                feature=pat.feature,
                recommended_value=pat.value,
                title=f"Use {human_val} for {feat_label}",
                explanation=pat.explanation,
                confidence=pat.confidence,
                evidence=pat.evidence,
                pattern_type=self._feature_to_pattern_type(pat.feature),
            ))
            seen_features.add(pat.feature)

            if len(recommendations) >= self.config.max_recommendations:
                break

        return recommendations

    def not_enough_data(self) -> bool:
        """Return True if we don't have enough data for any patterns."""
        return self._total_count < self.config.min_sample_count

    # ── Private helpers ───────────────────────────────────────────

    def _compute_confidence(self, row: AggregateRow, group_size: int) -> float:
        """Confidence score: combination of sample size and effect consistency."""
        # Sample factor: sqrt(count) / sqrt(count + 10), saturates near 1.0
        sample_factor = math.sqrt(row.raw_count) / math.sqrt(row.raw_count + 10)

        # Decay factor: how much recent weight we have
        recency_factor = min(row.decayed_count / max(row.raw_count, 1), 1.0)

        # Consistency: inverse of coefficient of variation (lower spread = higher)
        if row.mean_reward > 1e-9 and row.std_dev > 0:
            cv = row.std_dev / row.mean_reward
            consistency = 1.0 / (1.0 + cv)
        else:
            consistency = 0.5

        return round(sample_factor * 0.4 + recency_factor * 0.3 + consistency * 0.3, 4)

    def _classify_direction(
        self, mean_reward: float, baseline: float
    ) -> str | None:
        """Classify whether a value is winning, losing, or neutral."""
        if baseline < 1e-9:
            return None
        ratio = mean_reward / baseline
        if ratio >= self.config.winning_threshold_factor:
            return "winning"
        if ratio <= self.config.losing_threshold_factor:
            return "losing"
        return None

    def _generate_explanation(
        self,
        feature: str,
        row: AggregateRow,
        direction: str,
        baseline: float,
    ) -> tuple[str, str]:
        """Generate human-readable title and explanation."""
        feat_label = _FEATURE_LABELS.get(feature, feature.replace("_", " "))
        human_val = _human_value(feature, row.value)

        if baseline > 1e-9:
            lift_pct = (row.mean_reward / baseline - 1.0) * 100
        else:
            lift_pct = 0.0

        if direction == "winning":
            title = f"{human_val} ({feat_label}) performs well"
            explanation = (
                f"Posts using {human_val} for {feat_label} score "
                f"{abs(lift_pct):.0f}% {'above' if lift_pct > 0 else 'below'} "
                f"average based on {row.raw_count} observations. "
                f"Average reward: {row.mean_reward:.4f} vs baseline {baseline:.4f}."
            )
        else:
            title = f"{human_val} ({feat_label}) underperforms"
            explanation = (
                f"Posts using {human_val} for {feat_label} score "
                f"{abs(lift_pct):.0f}% below average based on "
                f"{row.raw_count} observations. "
                f"Average reward: {row.mean_reward:.4f} vs baseline {baseline:.4f}. "
                f"Consider alternatives."
            )

        return title, explanation

    @staticmethod
    def _feature_to_pattern_type(feature: str) -> str:
        """Map feature name to pattern_type category."""
        if feature.startswith(("post_hour", "post_day", "timing_")):
            return "timing"
        if feature.startswith(("content_", "caption_")):
            return "content"
        if feature.startswith(("asset_",)):
            return "content"
        if feature.startswith(("channel_",)):
            return "platform"
        if feature.startswith(("campaign_",)):
            return "campaign"
        return "content"

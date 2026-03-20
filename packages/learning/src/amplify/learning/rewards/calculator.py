"""Reward calculator — phase-aware, explainable, configurable.

Converts raw outcome metrics into a single scalar reward with full breakdown.
Every weight is visible, every normalization is documented, every computation
is auditable.  Supports phase-specific weighting (pre-release values presaves
over reach, release-day values streams over saves, etc.) and per-tenant
overrides via the weight_overrides mechanism.

Design goals:
- Optimize for real audience growth, not vanity metrics
- Make every reward explainable
- Allow future A/B testing of reward formulas
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ── Normalization ceilings ────────────────────────────────────────
# Each metric is normalized to [0, 1] against a platform-typical ceiling.
# These are intentionally generous so that "good" posts score 0.3-0.7
# and only truly viral content approaches 1.0.

NORM_CEILINGS: dict[str, float] = {
    "engagement_rate": 0.10,      # 10% engagement is exceptional
    "save_rate": 0.05,            # 5% save rate is exceptional
    "share_rate": 0.03,           # 3% share rate is exceptional
    "comment_rate": 0.05,         # 5% comment rate is exceptional
    "click_through_rate": 0.03,   # 3% CTR is exceptional
    "follow_rate": 0.01,          # 1% follower conversion is exceptional
    "completion_rate": 1.0,       # Already 0-1
    "email_capture_rate": 0.02,   # 2% email capture is exceptional
    "conversion_rate": 0.05,      # 5% downstream conversion is exceptional
}

# Log-scale normalization for absolute counts
LOG_CEILINGS: dict[str, float] = {
    "reach": 5.0,          # log10(100k) = 5
    "impressions": 5.5,    # log10(~300k) = 5.5
    "watch_time": 4.0,     # log10(10k minutes) = 4
}


# ── Weight profiles ───────────────────────────────────────────────


@dataclass(frozen=True)
class RewardWeights:
    """Weight profile for a specific campaign phase or context.

    All weights are relative — they are normalized to sum to 1.0 at
    compute time.  Set a weight to 0.0 to exclude a metric entirely.
    """

    # Audience growth signals (high value)
    followers_gained: float = 0.15
    email_capture_rate: float = 0.0    # only nonzero when CTA is email
    conversion_rate: float = 0.0       # downstream conversion proxy

    # Engagement quality signals (medium-high value)
    save_rate: float = 0.20
    share_rate: float = 0.15
    comment_rate: float = 0.05

    # Reach and awareness signals (medium value)
    engagement_rate: float = 0.15
    reach: float = 0.10
    click_through_rate: float = 0.10

    # Content consumption signals (medium value)
    completion_rate: float = 0.05
    watch_time: float = 0.05

    # Vanity metrics (low weight by default)
    impressions: float = 0.0

    def to_dict(self) -> dict[str, float]:
        """Return weights as a dict, excluding zero weights."""
        return {
            k: v for k, v in {
                "followers_gained": self.followers_gained,
                "email_capture_rate": self.email_capture_rate,
                "conversion_rate": self.conversion_rate,
                "save_rate": self.save_rate,
                "share_rate": self.share_rate,
                "comment_rate": self.comment_rate,
                "engagement_rate": self.engagement_rate,
                "reach": self.reach,
                "click_through_rate": self.click_through_rate,
                "completion_rate": self.completion_rate,
                "watch_time": self.watch_time,
                "impressions": self.impressions,
            }.items() if v > 0
        }

    @classmethod
    def from_dict(cls, d: dict[str, float]) -> RewardWeights:
        """Build weights from a dict, using defaults for missing keys."""
        defaults = cls()
        return cls(**{
            k: d.get(k, getattr(defaults, k))
            for k in defaults.__dataclass_fields__
        })


# ── Phase-specific presets ────────────────────────────────────────


def pre_release_weights() -> RewardWeights:
    """Pre-release: presaves, email captures, follower growth matter most."""
    return RewardWeights(
        followers_gained=0.20,
        email_capture_rate=0.15,
        save_rate=0.20,        # presave ≈ save intent
        share_rate=0.15,
        comment_rate=0.05,
        engagement_rate=0.10,
        reach=0.10,
        click_through_rate=0.05,
        completion_rate=0.0,
        watch_time=0.0,
        conversion_rate=0.0,
        impressions=0.0,
    )


def release_day_weights() -> RewardWeights:
    """Release day: clicks (to streaming), shares, conversion matter most."""
    return RewardWeights(
        followers_gained=0.10,
        email_capture_rate=0.0,
        save_rate=0.10,
        share_rate=0.20,
        comment_rate=0.05,
        engagement_rate=0.10,
        reach=0.15,
        click_through_rate=0.20,  # clicks to streaming link
        completion_rate=0.0,
        watch_time=0.0,
        conversion_rate=0.10,
        impressions=0.0,
    )


def post_release_weights() -> RewardWeights:
    """Post-release / sustain: sustained engagement and saves matter most."""
    return RewardWeights(
        followers_gained=0.15,
        email_capture_rate=0.0,
        save_rate=0.25,         # saves = long-tail discovery
        share_rate=0.15,
        comment_rate=0.10,
        engagement_rate=0.15,
        reach=0.10,
        click_through_rate=0.05,
        completion_rate=0.05,
        watch_time=0.0,
        conversion_rate=0.0,
        impressions=0.0,
    )


def evergreen_weights() -> RewardWeights:
    """Evergreen content: engagement depth and follower growth."""
    return RewardWeights(
        followers_gained=0.20,
        email_capture_rate=0.0,
        save_rate=0.20,
        share_rate=0.15,
        comment_rate=0.15,
        engagement_rate=0.15,
        reach=0.05,
        click_through_rate=0.05,
        completion_rate=0.05,
        watch_time=0.0,
        conversion_rate=0.0,
        impressions=0.0,
    )


PHASE_PRESETS: dict[str, RewardWeights] = {
    "early_announce": pre_release_weights(),
    "pre_release": pre_release_weights(),
    "countdown": pre_release_weights(),
    "release_day": release_day_weights(),
    "release_week": release_day_weights(),
    "post_release": post_release_weights(),
    "sustain": post_release_weights(),
    "evergreen": evergreen_weights(),
}


def weights_for_phase(phase: str | None) -> RewardWeights:
    """Return the weight profile for a campaign/release phase."""
    if phase and phase in PHASE_PRESETS:
        return PHASE_PRESETS[phase]
    return RewardWeights()  # defaults


# ── Outcome container ─────────────────────────────────────────────


@dataclass
class OutcomeData:
    """Raw metrics input for reward computation.

    Accepts all possible metrics.  Missing values are None and excluded
    from the weighted sum rather than treated as zero.
    """

    # Absolute counts
    impressions: int | None = None
    reach: int | None = None
    engagements: int | None = None
    saves: int | None = None
    shares: int | None = None
    clicks: int | None = None
    comments: int | None = None
    followers_gained: int | None = None

    # Derived rates (pre-computed or will be computed from counts)
    engagement_rate: float | None = None
    save_rate: float | None = None
    share_rate: float | None = None
    comment_rate: float | None = None
    click_through_rate: float | None = None
    follow_rate: float | None = None

    # Content consumption
    watch_time_seconds: float | None = None
    completion_rate: float | None = None

    # Downstream conversion proxies
    email_captures: int | None = None
    email_capture_rate: float | None = None
    conversions: int | None = None
    conversion_rate: float | None = None


# ── Reward breakdown ──────────────────────────────────────────────


@dataclass
class RewardBreakdown:
    """Full breakdown of how a reward was computed.

    Every field is present for auditability — rule-based systems read the
    components directly, ML systems use final_reward as the training signal.
    """

    raw_components: dict[str, float]       # metric → normalized [0,1] value
    weights_used: dict[str, float]         # metric → weight (pre-normalization)
    weighted_components: dict[str, float]  # metric → weighted contribution
    raw_total: float                       # sum of weighted components
    decay_factor: float                    # temporal decay multiplier
    completeness: float                    # fraction of expected metrics present
    confidence: float                      # completeness-adjusted confidence
    final_reward: float                    # raw_total * decay * confidence
    phase: str | None                      # which phase preset was used
    formula_version: str                   # for A/B testing

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON storage in reward_breakdown column."""
        return {
            "raw_components": self.raw_components,
            "weights_used": self.weights_used,
            "weighted_components": self.weighted_components,
            "raw_total": round(self.raw_total, 6),
            "decay_factor": round(self.decay_factor, 6),
            "completeness": round(self.completeness, 4),
            "confidence": round(self.confidence, 4),
            "final_reward": round(self.final_reward, 6),
            "phase": self.phase,
            "formula_version": self.formula_version,
        }


FORMULA_VERSION = "v2"


# ── Calculator ────────────────────────────────────────────────────


class RewardCalculator:
    """Computes a composite reward score from outcome metrics.

    The reward is a weighted sum of available metrics, normalized to [0, 1],
    with phase-aware weighting, temporal decay, and incomplete-window
    confidence adjustment.

    Usage:
        calc = RewardCalculator()
        breakdown = calc.compute(outcomes, phase="pre_release")
        # breakdown.final_reward is the scalar reward
        # breakdown.to_dict() is the full audit trail
    """

    def __init__(
        self,
        config: Any | None = None,
        weights: RewardWeights | None = None,
    ) -> None:
        # Accept legacy RewardConfig for backward compatibility
        if config is not None:
            self._legacy_config = config
        else:
            self._legacy_config = None
        self._default_weights = weights

    def compute(
        self,
        outcomes: Any,
        observed_at: datetime | None = None,
        now: datetime | None = None,
        phase: str | None = None,
        weight_overrides: dict[str, float] | None = None,
        measurement_window: str | None = None,
    ) -> RewardBreakdown:
        """Compute reward from outcome metrics.

        Args:
            outcomes: OutcomeData or legacy OutcomeMetrics
            observed_at: When the post was published (for temporal decay)
            now: Current time (defaults to utcnow)
            phase: Campaign/release phase for weight selection
            weight_overrides: Per-tenant weight overrides (merged on top of phase weights)
            measurement_window: e.g. "24h", "48h", "7d" — affects confidence
        """
        data = self._to_outcome_data(outcomes)
        data = self._compute_derived_rates(data)

        weights = self._resolve_weights(phase, weight_overrides)
        active_weights = weights.to_dict()

        raw, available_count = self._normalize(data, active_weights)

        # Weighted sum
        total_weight = sum(active_weights.values()) or 1.0
        weighted: dict[str, float] = {}
        raw_total = 0.0
        for key, value in raw.items():
            w = active_weights.get(key, 0.0) / total_weight
            contribution = round(value * w, 6)
            weighted[key] = contribution
            raw_total += contribution

        # Temporal decay
        decay = self._decay_factor(observed_at, now)

        # Completeness and confidence
        expected = len(active_weights)
        completeness = available_count / expected if expected > 0 else 0.0
        confidence = self._window_confidence(measurement_window, completeness)

        final = raw_total * decay * confidence

        return RewardBreakdown(
            raw_components=raw,
            weights_used=active_weights,
            weighted_components=weighted,
            raw_total=round(raw_total, 6),
            decay_factor=round(decay, 6),
            completeness=round(completeness, 4),
            confidence=round(confidence, 4),
            final_reward=round(final, 6),
            phase=phase,
            formula_version=FORMULA_VERSION,
        )

    # ── Weight resolution ─────────────────────────────────────────

    def _resolve_weights(
        self,
        phase: str | None,
        overrides: dict[str, float] | None,
    ) -> RewardWeights:
        """Resolve final weights: phase preset → default → overrides."""
        if self._default_weights is not None:
            base = self._default_weights
        elif self._legacy_config is not None:
            # Backward compat: convert old RewardConfig to RewardWeights
            base = RewardWeights(
                engagement_rate=self._legacy_config.engagement_weight,
                reach=self._legacy_config.reach_weight,
                save_rate=self._legacy_config.save_rate_weight,
                click_through_rate=self._legacy_config.click_through_weight,
                followers_gained=0,
                share_rate=0,
                comment_rate=0,
                completion_rate=0,
                watch_time=0,
                impressions=0,
                email_capture_rate=0,
                conversion_rate=0,
            )
        else:
            base = weights_for_phase(phase)

        if not overrides:
            return base

        merged = {k: getattr(base, k) for k in base.__dataclass_fields__}
        merged.update(overrides)
        return RewardWeights(**merged)

    # ── Normalization ─────────────────────────────────────────────

    def _normalize(
        self,
        data: OutcomeData,
        active_weights: dict[str, float],
    ) -> tuple[dict[str, float], int]:
        """Normalize available metrics to [0, 1]. Return (normalized, count_available)."""
        raw: dict[str, float] = {}
        count = 0

        for metric_name in active_weights:
            value = self._get_metric_value(data, metric_name)
            if value is None:
                continue

            if metric_name in LOG_CEILINGS:
                ceiling = LOG_CEILINGS[metric_name]
                normalized = min(math.log10(max(value, 1)) / ceiling, 1.0)
            elif metric_name in NORM_CEILINGS:
                ceiling = NORM_CEILINGS[metric_name]
                normalized = min(value / ceiling, 1.0) if ceiling > 0 else 0.0
            elif metric_name == "followers_gained":
                # Log-scale: 100 followers ≈ 0.5, 10k ≈ 1.0
                normalized = min(math.log10(max(value, 1)) / 4.0, 1.0)
            else:
                normalized = min(float(value), 1.0)

            raw[metric_name] = round(normalized, 6)
            count += 1

        return raw, count

    @staticmethod
    def _get_metric_value(data: OutcomeData, metric_name: str) -> float | None:
        """Extract a metric value from OutcomeData by name."""
        mapping = {
            "engagement_rate": data.engagement_rate,
            "save_rate": data.save_rate,
            "share_rate": data.share_rate,
            "comment_rate": data.comment_rate,
            "click_through_rate": data.click_through_rate,
            "follow_rate": data.follow_rate,
            "completion_rate": data.completion_rate,
            "email_capture_rate": data.email_capture_rate,
            "conversion_rate": data.conversion_rate,
            "reach": data.reach,
            "impressions": data.impressions,
            "watch_time": data.watch_time_seconds,
            "followers_gained": data.followers_gained,
        }
        return mapping.get(metric_name)

    # ── Derived rates ─────────────────────────────────────────────

    @staticmethod
    def _compute_derived_rates(data: OutcomeData) -> OutcomeData:
        """Fill in derived rates from raw counts when not pre-computed."""
        base = data.impressions or data.reach or 0
        if base == 0:
            return data

        if data.engagement_rate is None and data.engagements is not None:
            data.engagement_rate = data.engagements / base
        if data.save_rate is None and data.saves is not None:
            data.save_rate = data.saves / base
        if data.share_rate is None and data.shares is not None:
            data.share_rate = data.shares / base
        if data.comment_rate is None and data.comments is not None:
            data.comment_rate = data.comments / base
        if data.click_through_rate is None and data.clicks is not None:
            data.click_through_rate = data.clicks / base
        if data.follow_rate is None and data.followers_gained is not None:
            data.follow_rate = data.followers_gained / base
        if data.email_capture_rate is None and data.email_captures is not None:
            data.email_capture_rate = data.email_captures / base
        if data.conversion_rate is None and data.conversions is not None:
            data.conversion_rate = data.conversions / base

        return data

    # ── Temporal decay ────────────────────────────────────────────

    @staticmethod
    def _decay_factor(
        observed_at: datetime | None,
        now: datetime | None,
        half_life_days: float = 30.0,
    ) -> float:
        """Exponential decay based on observation age."""
        if observed_at is None:
            return 1.0
        now = now or datetime.utcnow()
        age_days = (now - observed_at).total_seconds() / 86400
        if age_days <= 0:
            return 1.0
        return math.exp(-0.693 * age_days / half_life_days)

    # ── Incomplete window confidence ──────────────────────────────

    @staticmethod
    def _window_confidence(
        measurement_window: str | None,
        completeness: float,
    ) -> float:
        """Adjust confidence based on measurement window and data completeness.

        Early windows (1h, 3h, 6h) have lower confidence because metrics
        haven't stabilized.  Missing metrics further reduce confidence.
        """
        # Window maturity factor
        window_maturity = {
            "1h": 0.3,
            "3h": 0.5,
            "6h": 0.65,
            "12h": 0.8,
            "24h": 0.9,
            "48h": 0.95,
            "7d": 1.0,
            "14d": 1.0,
            "final": 1.0,
        }
        maturity = window_maturity.get(measurement_window or "final", 1.0)

        # Combine: sqrt(completeness) softens the penalty for missing 1-2 metrics
        data_factor = math.sqrt(completeness) if completeness > 0 else 0.0

        return round(maturity * data_factor, 4)

    # ── Legacy compatibility ──────────────────────────────────────

    @staticmethod
    def _to_outcome_data(outcomes: Any) -> OutcomeData:
        """Convert legacy OutcomeMetrics to OutcomeData, or pass through."""
        if isinstance(outcomes, OutcomeData):
            return outcomes
        # Duck-type: OutcomeMetrics has the same field names
        return OutcomeData(
            impressions=getattr(outcomes, "impressions", None),
            reach=getattr(outcomes, "reach", None),
            engagements=getattr(outcomes, "engagements", None),
            saves=getattr(outcomes, "saves", None),
            shares=getattr(outcomes, "shares", None),
            clicks=getattr(outcomes, "clicks", None),
            comments=getattr(outcomes, "comments", getattr(outcomes, "comments_count", None)),
            followers_gained=getattr(outcomes, "followers_gained", None),
            engagement_rate=getattr(outcomes, "engagement_rate", None),
            save_rate=getattr(outcomes, "save_rate", None),
            click_through_rate=getattr(outcomes, "click_through_rate", None),
        )

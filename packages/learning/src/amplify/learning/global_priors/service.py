"""Global prior service — privacy-safe cross-tenant intelligence.

Builds on top of PriorAggregator and CohortEngine to produce
actionable, de-identified recommendations for hook families,
posting windows, CTA types, and format preferences.

Privacy guarantees:
- All outputs are aggregate statistics, never tenant-specific
- Minimum k-anonymity enforced before any pattern is surfaced
- Redacted fields (content_text, artist_name, campaign_name, tenant_id)
  are stripped before aggregation
- Recommendations are always labeled as global priors, not tenant-learned
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from amplify.learning.config import LearningConfig, PrivacyConfig
from amplify.learning.global_priors.prior import GlobalPrior
from amplify.learning.schemas.observation import Observation


# ── Pattern categories ─────────────────────────────────────────────

PATTERN_CATEGORIES = (
    "posting_window",
    "cta_type",
    "format_preference",
    "hook_family",
)

# Maps category → which observation feature(s) to aggregate
CATEGORY_FEATURE_MAP: dict[str, list[str]] = {
    "posting_window": ["post_hour", "post_day_of_week"],
    "cta_type": ["cta_type"],
    "format_preference": ["content_type", "format"],
    "hook_family": ["hook_family", "hook_type"],
}


# ── Recommendation ─────────────────────────────────────────────────


@dataclass
class GlobalRecommendation:
    """A single privacy-safe recommendation derived from global priors.

    Always labeled as a global prior — never presented as tenant-learned.
    """

    category: str  # e.g. "posting_window", "cta_type"
    feature_name: str
    feature_value: str
    mean_reward: float
    observation_count: int
    tenant_count: int
    confidence: float  # 0.0–1.0 based on sample size and variance
    std_dev: float | None = None
    confidence_lower: float | None = None
    confidence_upper: float | None = None
    source: str = "global_prior"  # always "global_prior" or "cohort_prior"
    source_cohort_id: uuid.UUID | None = None
    source_cohort_name: str = ""
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "feature_name": self.feature_name,
            "feature_value": self.feature_value,
            "mean_reward": round(self.mean_reward, 6),
            "observation_count": self.observation_count,
            "tenant_count": self.tenant_count,
            "confidence": round(self.confidence, 4),
            "std_dev": round(self.std_dev, 6) if self.std_dev is not None else None,
            "confidence_lower": (
                round(self.confidence_lower, 6) if self.confidence_lower is not None else None
            ),
            "confidence_upper": (
                round(self.confidence_upper, 6) if self.confidence_upper is not None else None
            ),
            "source": self.source,
            "source_cohort_id": str(self.source_cohort_id) if self.source_cohort_id else None,
            "source_cohort_name": self.source_cohort_name,
            "explanation": self.explanation,
        }


# ── De-identification ──────────────────────────────────────────────


def redact_observation(obs: Observation, privacy: PrivacyConfig) -> dict[str, Any]:
    """Strip PII fields from an observation's features dict.

    Returns a new features dict with redacted_fields removed.
    Never returns raw content text, artist names, or tenant IDs.
    """
    return {
        k: v
        for k, v in obs.features.items()
        if k not in privacy.redacted_fields
    }


def validate_no_pii(data: dict[str, Any], privacy: PrivacyConfig) -> list[str]:
    """Check that a dict does not contain any redacted fields.

    Returns a list of violations (empty = clean).
    """
    violations = []
    for field_name in privacy.redacted_fields:
        if field_name in data:
            violations.append(f"Redacted field '{field_name}' present in output")
    # Also check for raw content patterns
    for key, value in data.items():
        if isinstance(value, str) and len(value) > 200:
            violations.append(
                f"Field '{key}' contains long text ({len(value)} chars) — possible raw content"
            )
    return violations


# ── Threshold gating ───────────────────────────────────────────────


@dataclass(frozen=True)
class ThresholdConfig:
    """Minimum thresholds before a pattern becomes eligible for reuse."""

    min_observations: int = 30
    min_tenant_count: int = 5
    min_confidence: float = 0.3
    max_categorical_cardinality: int = 50


def passes_threshold(
    observation_count: int,
    tenant_count: int,
    confidence: float,
    config: ThresholdConfig,
) -> bool:
    """Check if a pattern meets all minimum thresholds for cross-tenant reuse."""
    return (
        observation_count >= config.min_observations
        and tenant_count >= config.min_tenant_count
        and confidence >= config.min_confidence
    )


def compute_confidence(
    observation_count: int,
    tenant_count: int,
    std_dev: float | None,
) -> float:
    """Compute a 0–1 confidence score from sample statistics.

    Higher with more observations, more tenants, and lower variance.
    """
    if observation_count == 0 or tenant_count == 0:
        return 0.0

    # Sample size component: saturates at 200 observations
    obs_score = min(observation_count / 200.0, 1.0)

    # Tenant diversity component: saturates at 20 tenants
    tenant_score = min(tenant_count / 20.0, 1.0)

    # Precision component: low std_dev → high confidence
    if std_dev is not None and std_dev > 0:
        # Coefficient of variation penalty
        precision_score = max(0.0, 1.0 - std_dev)
    else:
        precision_score = 0.5  # unknown variance → moderate confidence

    return obs_score * 0.4 + tenant_score * 0.3 + precision_score * 0.3


# ── Global prior service ──────────────────────────────────────────


class GlobalPriorService:
    """Builds privacy-safe global recommendations from cross-tenant data.

    Stateless engine — all data comes in via arguments, results come out
    via return values. No DB dependency.
    """

    def __init__(self, config: LearningConfig | None = None) -> None:
        self.config = config or LearningConfig()
        self.privacy = self.config.privacy
        self.thresholds = ThresholdConfig(
            min_observations=self.config.rewards.min_observations * 3,
            min_tenant_count=self.config.privacy.k_anonymity_threshold,
            max_categorical_cardinality=self.config.privacy.max_categorical_cardinality,
        )

    def aggregate_category(
        self,
        category: str,
        observations: list[Observation],
        action_type: str = "",
        source_cohort_id: uuid.UUID | None = None,
        source_cohort_name: str = "",
    ) -> list[GlobalRecommendation]:
        """Aggregate observations for a pattern category.

        Returns recommendations that pass all thresholds and privacy checks.
        """
        if not self.privacy.cross_tenant_enabled:
            return []

        feature_names = CATEGORY_FEATURE_MAP.get(category, [])
        if not feature_names:
            return []

        recommendations: list[GlobalRecommendation] = []

        for feature_name in feature_names:
            recs = self._aggregate_feature(
                feature_name=feature_name,
                observations=observations,
                action_type=action_type,
                category=category,
                source_cohort_id=source_cohort_id,
                source_cohort_name=source_cohort_name,
            )
            recommendations.extend(recs)

        return sorted(recommendations, key=lambda r: r.mean_reward, reverse=True)

    def aggregate_all_categories(
        self,
        observations: list[Observation],
        action_type: str = "",
        source_cohort_id: uuid.UUID | None = None,
        source_cohort_name: str = "",
    ) -> dict[str, list[GlobalRecommendation]]:
        """Aggregate observations across all pattern categories."""
        results: dict[str, list[GlobalRecommendation]] = {}
        for category in PATTERN_CATEGORIES:
            recs = self.aggregate_category(
                category, observations, action_type,
                source_cohort_id, source_cohort_name,
            )
            if recs:
                results[category] = recs
        return results

    def get_cold_start_recommendations(
        self,
        tenant_observation_count: int,
        global_recommendations: dict[str, list[GlobalRecommendation]],
        cold_start_threshold: int | None = None,
    ) -> dict[str, list[GlobalRecommendation]]:
        """Filter recommendations for a cold-start tenant.

        Returns only categories where the tenant has insufficient data
        and the global priors are reliable enough to recommend.
        """
        threshold = cold_start_threshold or self.config.ranking.cold_start_threshold

        if tenant_observation_count >= threshold:
            return {}  # tenant has enough data, no cold-start needed

        results: dict[str, list[GlobalRecommendation]] = {}
        for category, recs in global_recommendations.items():
            reliable = [r for r in recs if r.confidence >= self.thresholds.min_confidence]
            if reliable:
                # Add cold-start explanation
                for r in reliable:
                    r.explanation = (
                        f"Based on aggregate data from {r.tenant_count} similar accounts. "
                        f"This is a global prior, not learned from your specific data."
                    )
                results[category] = reliable

        return results

    def de_identify_observations(
        self,
        observations: list[Observation],
    ) -> list[dict[str, Any]]:
        """Strip PII from observations, returning only safe feature dicts.

        This is the privacy boundary — nothing downstream should have
        access to redacted fields.
        """
        safe: list[dict[str, Any]] = []
        for obs in observations:
            features = redact_observation(obs, self.privacy)
            safe.append({
                "features": features,
                "reward": obs.reward,
                "action_type": obs.action_type,
            })
        return safe

    # ── Internal ──────────────────────────────────────────────────

    def _aggregate_feature(
        self,
        feature_name: str,
        observations: list[Observation],
        action_type: str,
        category: str,
        source_cohort_id: uuid.UUID | None,
        source_cohort_name: str,
    ) -> list[GlobalRecommendation]:
        """Aggregate a single feature across observations."""
        # Group by feature value, tracking tenant_ids
        buckets: dict[str, list[_ObsBucket]] = defaultdict(list)

        for obs in observations:
            if action_type and obs.action_type != action_type:
                continue
            # Redact before accessing features
            safe_features = redact_observation(obs, self.privacy)
            value = safe_features.get(feature_name)
            if value is None or obs.reward is None:
                continue
            str_value = str(value)
            buckets[str_value].append(_ObsBucket(
                tenant_id=obs.tenant_id,
                reward=obs.reward,
            ))

        # Cardinality check — too many distinct values risks re-identification
        if len(buckets) > self.thresholds.max_categorical_cardinality:
            return []

        recommendations: list[GlobalRecommendation] = []

        for value, bucket_list in buckets.items():
            tenant_ids = {b.tenant_id for b in bucket_list}
            rewards = [b.reward for b in bucket_list]
            n = len(rewards)

            if n == 0:
                continue

            mean = sum(rewards) / n
            std = _std_dev(rewards) if n > 1 else None
            confidence = compute_confidence(n, len(tenant_ids), std)

            # Threshold gating
            if not passes_threshold(n, len(tenant_ids), confidence, self.thresholds):
                continue

            ci_lower = mean - 1.96 * (std / math.sqrt(n)) if std else None
            ci_upper = mean + 1.96 * (std / math.sqrt(n)) if std else None

            source = "cohort_prior" if source_cohort_id else "global_prior"

            recommendations.append(GlobalRecommendation(
                category=category,
                feature_name=feature_name,
                feature_value=value,
                mean_reward=mean,
                observation_count=n,
                tenant_count=len(tenant_ids),
                confidence=confidence,
                std_dev=std,
                confidence_lower=ci_lower,
                confidence_upper=ci_upper,
                source=source,
                source_cohort_id=source_cohort_id,
                source_cohort_name=source_cohort_name,
                explanation=(
                    f"Across {len(tenant_ids)} accounts, {feature_name}=\"{value}\" "
                    f"averages {mean:.3f} reward ({source.replace('_', ' ')})."
                ),
            ))

        return recommendations


# ── Internal helpers ──────────────────────────────────────────────


class _ObsBucket:
    __slots__ = ("tenant_id", "reward")

    def __init__(self, tenant_id: uuid.UUID, reward: float) -> None:
        self.tenant_id = tenant_id
        self.reward = reward


def _std_dev(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    return math.sqrt(variance)

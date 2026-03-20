"""Recommendation blending engine.

Merges tenant-specific recommendations with global priors based on
observation count. New tenants see 100% global priors; as local data
accumulates, tenant-learned patterns gradually take over.

The blend uses a linear ramp identical to the ranker's weight schedule:
  - 0 observations → tenant_weight=0.0  (100% global)
  - cold_start_threshold observations → tenant_weight=0.7  (30% global)
  - Beyond threshold: global priors still contribute at tenant_prior_blend

Every recommendation carries a source label so the UI can distinguish
between global defaults and tenant-learned insights.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from amplify.learning.config import LearningConfig


class RecommendationSource(str, Enum):
    """Where a recommendation originated."""

    GLOBAL_PRIOR = "global_prior"
    COHORT_PRIOR = "cohort_prior"
    TENANT_LEARNED = "tenant_learned"
    BLENDED = "blended"


@dataclass
class BlendedRecommendation:
    """A single recommendation with source tracking and blend metadata."""

    feature: str
    recommended_value: str
    title: str
    explanation: str
    confidence: float
    source: RecommendationSource
    category: str = ""
    evidence: dict = field(default_factory=dict)
    pattern_type: str = ""

    # Blend metadata
    tenant_weight: float = 0.0
    global_weight: float = 1.0
    tenant_confidence: float | None = None
    global_confidence: float | None = None
    global_tenant_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "recommended_value": self.recommended_value,
            "title": self.title,
            "explanation": self.explanation,
            "confidence": round(self.confidence, 4),
            "source": self.source.value,
            "category": self.category,
            "evidence": self.evidence,
            "pattern_type": self.pattern_type,
            "tenant_weight": round(self.tenant_weight, 4),
            "global_weight": round(self.global_weight, 4),
            "tenant_confidence": (
                round(self.tenant_confidence, 4)
                if self.tenant_confidence is not None
                else None
            ),
            "global_confidence": (
                round(self.global_confidence, 4)
                if self.global_confidence is not None
                else None
            ),
            "global_tenant_count": self.global_tenant_count,
        }


class BlendingEngine:
    """Blends tenant recommendations with global priors.

    Stateless — pass data in, get blended results out.
    """

    def __init__(self, config: LearningConfig | None = None) -> None:
        self.config = config or LearningConfig()
        self._ranking = self.config.ranking

    def compute_tenant_weight(self, observation_count: int) -> float:
        """Linear ramp from 0 to (1 - tenant_prior_blend).

        Matches Ranker._compute_tenant_weight exactly.
        """
        threshold = self._ranking.cold_start_threshold
        max_weight = 1.0 - self._ranking.tenant_prior_blend
        if observation_count == 0:
            return 0.0
        if observation_count >= threshold:
            return max_weight
        return (observation_count / threshold) * max_weight

    def blend(
        self,
        tenant_recs: list[dict[str, Any]],
        global_recs: dict[str, list[dict[str, Any]]],
        observation_count: int,
    ) -> list[BlendedRecommendation]:
        """Merge tenant recommendations with global priors.

        Args:
            tenant_recs: Recommendations from TenantPatternService.get_recommendations()
            global_recs: Grouped recommendations from DBGlobalPriorService.get_cold_start_recommendations()
            observation_count: Number of feature vectors for this tenant

        Returns:
            Unified list of BlendedRecommendation sorted by confidence desc.
        """
        tenant_weight = self.compute_tenant_weight(observation_count)
        global_weight = 1.0 - tenant_weight

        results: list[BlendedRecommendation] = []

        # Index tenant recs by feature for merging
        tenant_by_feature: dict[str, dict] = {}
        for rec in tenant_recs:
            key = rec.get("feature", "")
            tenant_by_feature[key] = rec

        # Track which features are covered by tenant data
        covered_features: set[str] = set()

        # 1) Process tenant recommendations
        for rec in tenant_recs:
            feature = rec.get("feature", "")
            covered_features.add(feature)

            # Find matching global rec
            global_match = self._find_global_match(
                feature, rec.get("recommended_value", ""), global_recs,
            )

            if global_match and global_weight > 0:
                # Blend confidence scores
                t_conf = rec.get("confidence", 0.5)
                g_conf = global_match.get("confidence", global_match.get("mean_reward", 0.5))
                blended_conf = t_conf * tenant_weight + g_conf * global_weight

                results.append(BlendedRecommendation(
                    feature=feature,
                    recommended_value=rec.get("recommended_value", ""),
                    title=rec.get("title", ""),
                    explanation=self._blend_explanation(
                        rec, global_match, tenant_weight,
                    ),
                    confidence=blended_conf,
                    source=RecommendationSource.BLENDED,
                    category=global_match.get("category", ""),
                    evidence=rec.get("evidence", {}),
                    pattern_type=rec.get("pattern_type", ""),
                    tenant_weight=tenant_weight,
                    global_weight=global_weight,
                    tenant_confidence=t_conf,
                    global_confidence=g_conf,
                    global_tenant_count=global_match.get("tenant_count"),
                ))
            else:
                # Pure tenant recommendation
                results.append(BlendedRecommendation(
                    feature=feature,
                    recommended_value=rec.get("recommended_value", ""),
                    title=rec.get("title", ""),
                    explanation=rec.get("explanation", ""),
                    confidence=rec.get("confidence", 0.5),
                    source=RecommendationSource.TENANT_LEARNED,
                    evidence=rec.get("evidence", {}),
                    pattern_type=rec.get("pattern_type", ""),
                    tenant_weight=tenant_weight,
                    global_weight=global_weight,
                    tenant_confidence=rec.get("confidence", 0.5),
                ))

        # 2) Add global priors that have no tenant equivalent
        for category, cat_recs in global_recs.items():
            for g_rec in cat_recs:
                feature = g_rec.get("feature_name", "")
                if feature in covered_features:
                    continue

                source_str = g_rec.get("source", "global_prior")
                source = (
                    RecommendationSource.COHORT_PRIOR
                    if source_str == "cohort_prior"
                    else RecommendationSource.GLOBAL_PRIOR
                )

                g_conf = g_rec.get("confidence", g_rec.get("mean_reward", 0.5))

                results.append(BlendedRecommendation(
                    feature=feature,
                    recommended_value=g_rec.get("feature_value", ""),
                    title=self._global_title(feature, g_rec.get("feature_value", "")),
                    explanation=g_rec.get(
                        "explanation",
                        f"Global default based on {g_rec.get('tenant_count', 0)} accounts.",
                    ),
                    confidence=g_conf * global_weight,
                    source=source,
                    category=category,
                    pattern_type=self._feature_to_pattern_type(feature),
                    tenant_weight=tenant_weight,
                    global_weight=global_weight,
                    global_confidence=g_conf,
                    global_tenant_count=g_rec.get("tenant_count"),
                ))

        # Sort by confidence descending
        results.sort(key=lambda r: r.confidence, reverse=True)
        return results

    def is_cold_start(self, observation_count: int) -> bool:
        """Whether a tenant is in the cold-start phase."""
        return observation_count < self._ranking.cold_start_threshold

    def data_maturity_label(self, observation_count: int) -> str:
        """Human-friendly label for data maturity."""
        threshold = self._ranking.cold_start_threshold
        if observation_count == 0:
            return "no_data"
        if observation_count < threshold // 2:
            return "early"
        if observation_count < threshold:
            return "growing"
        return "mature"

    # ── Internal ──────────────────────────────────────────────────

    def _find_global_match(
        self,
        feature: str,
        value: str,
        global_recs: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any] | None:
        """Find a global rec matching the same feature+value."""
        for _cat, recs in global_recs.items():
            for rec in recs:
                if rec.get("feature_name") == feature and rec.get("feature_value") == value:
                    return rec
        return None

    @staticmethod
    def _blend_explanation(
        tenant_rec: dict, global_rec: dict, tenant_weight: float,
    ) -> str:
        """Create an explanation describing the blend."""
        pct = round(tenant_weight * 100)
        tenant_count = global_rec.get("tenant_count", 0)
        return (
            f"{tenant_rec.get('explanation', '')} "
            f"({pct}% from your data, {100 - pct}% from {tenant_count} similar accounts)"
        )

    @staticmethod
    def _global_title(feature: str, value: str) -> str:
        """Generate a human-readable title for a global-only recommendation."""
        labels = {
            "post_hour": f"Best posting hour: {value}:00",
            "post_day_of_week": f"Best posting day: {_day_name(value)}",
            "cta_type": f"Top CTA style: {value}",
            "content_type": f"Best content format: {value}",
            "format": f"Best format: {value}",
            "hook_family": f"Top hook family: {value}",
            "hook_type": f"Top hook type: {value}",
        }
        return labels.get(feature, f"Recommended {feature}: {value}")

    @staticmethod
    def _feature_to_pattern_type(feature: str) -> str:
        """Map feature name to pattern type."""
        mapping = {
            "post_hour": "timing",
            "post_day_of_week": "timing",
            "cta_type": "cta",
            "content_type": "format",
            "format": "format",
            "hook_family": "hook",
            "hook_type": "hook",
        }
        return mapping.get(feature, "general")


def _day_name(value: str) -> str:
    """Convert day-of-week number to name."""
    days = {
        "0": "Monday", "1": "Tuesday", "2": "Wednesday",
        "3": "Thursday", "4": "Friday", "5": "Saturday", "6": "Sunday",
    }
    return days.get(str(value), str(value))

"""Cohort engine — definition, assignment, aggregation, cold-start priors.

This module is the core of the cohort modeling system.  It is pure logic
with no DB or framework dependencies, making it easy to test and reuse.

Privacy principles:
- Cohort outputs never contain tenant IDs, artist names, or content text
- Aggregation requires >= k distinct tenants per cohort (k-anonymity)
- Individual tenant contributions are not recoverable from outputs
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ── Cohort definition ─────────────────────────────────────────────


@dataclass
class CohortDefinition:
    """A named, versioned rule for grouping tenants.

    ``criteria`` is a flexible JSON DSL:
        {
            "genre_family": ["hip-hop", "r-and-b"],
            "audience_size_bucket": "small",
            "content_styles": {"includes_any": ["short-form-video", "reel"]},
            "release_type": "single",
            "channel_mix": {"includes_all": ["instagram", "tiktok"]},
        }

    Each key maps to a trait on TenantProfile.  Matching rules:
    - list value  → profile value must be in the list
    - str/int     → exact match
    - dict with "includes_any" → profile value (list) shares any element
    - dict with "includes_all" → profile value (list) contains all elements
    - dict with "min"/"max"    → numeric range check
    """

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = ""
    description: str = ""
    criteria: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def matches(self, profile: TenantProfile) -> bool:
        """Return True if the profile satisfies all criteria."""
        for trait_key, rule in self.criteria.items():
            value = profile.traits.get(trait_key)
            if not _matches_rule(value, rule):
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "criteria": self.criteria,
            "version": self.version,
            "is_active": self.is_active,
        }


# ── Tenant profile ───────────────────────────────────────────────


@dataclass
class TenantProfile:
    """Marketing traits for a single tenant — the input to cohort assignment.

    Traits are a flexible dict so new dimensions can be added without
    schema changes.  Common traits:
    - genre_family: str          ("hip-hop", "pop", "electronic", ...)
    - audience_size_bucket: str  ("tiny", "small", "medium", "large", "huge")
    - content_styles: list[str]  (["short-form-video", "carousel", "story"])
    - release_type: str          ("single", "ep", "album")
    - channel_mix: list[str]     (["instagram", "tiktok", "youtube"])
    - avg_engagement_rate: float
    - total_posts: int
    """

    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    traits: dict[str, Any] = field(default_factory=dict)
    observation_count: int = 0
    rewards: list[float] = field(default_factory=list)


# ── Cohort assignment ────────────────────────────────────────────


@dataclass
class CohortAssignment:
    """Records that a tenant belongs to a cohort."""

    tenant_id: uuid.UUID
    cohort_id: uuid.UUID
    cohort_name: str = ""
    assigned_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Cohort statistics ────────────────────────────────────────────


@dataclass
class CohortStats:
    """Privacy-safe aggregate statistics for one cohort."""

    cohort_id: uuid.UUID
    cohort_name: str
    tenant_count: int = 0
    total_observations: int = 0
    mean_reward: float = 0.0
    std_dev: float | None = None
    confidence_lower: float | None = None
    confidence_upper: float | None = None
    feature_stats: dict[str, Any] = field(default_factory=dict)
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "cohort_id": str(self.cohort_id),
            "cohort_name": self.cohort_name,
            "tenant_count": self.tenant_count,
            "total_observations": self.total_observations,
            "mean_reward": round(self.mean_reward, 6),
            "std_dev": round(self.std_dev, 6) if self.std_dev is not None else None,
            "confidence_lower": (
                round(self.confidence_lower, 6) if self.confidence_lower is not None else None
            ),
            "confidence_upper": (
                round(self.confidence_upper, 6) if self.confidence_upper is not None else None
            ),
            "feature_stats": self.feature_stats,
            "computed_at": self.computed_at.isoformat(),
        }


# ── Cohort prior (for cold-start) ───────────────────────────────


@dataclass
class CohortPrior:
    """A cold-start prior derived from cohort-level aggregation.

    Used to bootstrap ranking for new/low-data tenants by blending
    cohort priors with whatever tenant data exists.
    """

    feature_name: str
    feature_value: str
    mean_reward: float = 0.0
    observation_count: int = 0
    tenant_count: int = 0
    std_dev: float | None = None
    confidence_lower: float | None = None
    confidence_upper: float | None = None
    cohort_id: uuid.UUID | None = None
    cohort_name: str = ""

    @property
    def is_reliable(self) -> bool:
        return self.observation_count >= 10 and self.tenant_count >= 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_name": self.feature_name,
            "feature_value": self.feature_value,
            "mean_reward": round(self.mean_reward, 6),
            "observation_count": self.observation_count,
            "tenant_count": self.tenant_count,
            "std_dev": round(self.std_dev, 6) if self.std_dev is not None else None,
            "confidence_lower": (
                round(self.confidence_lower, 6) if self.confidence_lower is not None else None
            ),
            "confidence_upper": (
                round(self.confidence_upper, 6) if self.confidence_upper is not None else None
            ),
            "cohort_id": str(self.cohort_id) if self.cohort_id else None,
            "cohort_name": self.cohort_name,
        }


# ── Cohort engine ────────────────────────────────────────────────


class CohortEngine:
    """Assigns tenants to cohorts, aggregates outcomes, computes priors.

    All methods are stateless — state comes in via arguments, results
    come out via return values.
    """

    def __init__(self, k_anonymity_threshold: int = 5) -> None:
        self.k = k_anonymity_threshold

    # ── Assignment ────────────────────────────────────────────────

    def assign_tenants(
        self,
        profiles: list[TenantProfile],
        definitions: list[CohortDefinition],
    ) -> list[CohortAssignment]:
        """Assign each tenant profile to all matching cohort definitions.

        A tenant may belong to multiple cohorts (e.g. "hip-hop" AND "small audience").
        """
        assignments: list[CohortAssignment] = []
        for profile in profiles:
            for defn in definitions:
                if not defn.is_active:
                    continue
                if defn.matches(profile):
                    assignments.append(CohortAssignment(
                        tenant_id=profile.tenant_id,
                        cohort_id=defn.id,
                        cohort_name=defn.name,
                    ))
        return assignments

    # ── Aggregation ───────────────────────────────────────────────

    def aggregate_cohort(
        self,
        definition: CohortDefinition,
        profiles: list[TenantProfile],
    ) -> CohortStats | None:
        """Aggregate normalized outcomes for one cohort.

        Returns None if the cohort has fewer than k distinct tenants
        (k-anonymity enforcement).
        """
        members = [p for p in profiles if definition.matches(p)]
        tenant_ids = {p.tenant_id for p in members}

        if len(tenant_ids) < self.k:
            return None

        all_rewards: list[float] = []
        total_obs = 0
        for p in members:
            all_rewards.extend(p.rewards)
            total_obs += p.observation_count

        if not all_rewards:
            return CohortStats(
                cohort_id=definition.id,
                cohort_name=definition.name,
                tenant_count=len(tenant_ids),
                total_observations=total_obs,
            )

        mean = sum(all_rewards) / len(all_rewards)
        std = _std_dev(all_rewards) if len(all_rewards) > 1 else None

        return CohortStats(
            cohort_id=definition.id,
            cohort_name=definition.name,
            tenant_count=len(tenant_ids),
            total_observations=total_obs,
            mean_reward=mean,
            std_dev=std,
            confidence_lower=(
                mean - 1.96 * (std / math.sqrt(len(all_rewards))) if std else None
            ),
            confidence_upper=(
                mean + 1.96 * (std / math.sqrt(len(all_rewards))) if std else None
            ),
        )

    def aggregate_all(
        self,
        definitions: list[CohortDefinition],
        profiles: list[TenantProfile],
    ) -> list[CohortStats]:
        """Aggregate all active cohorts. Skips those below k-anonymity."""
        stats: list[CohortStats] = []
        for defn in definitions:
            if not defn.is_active:
                continue
            result = self.aggregate_cohort(defn, profiles)
            if result is not None:
                stats.append(result)
        return stats

    # ── Cold-start priors ─────────────────────────────────────────

    def compute_cold_start_priors(
        self,
        tenant_profile: TenantProfile,
        definitions: list[CohortDefinition],
        all_profiles: list[TenantProfile],
        feature_names: list[str] | None = None,
    ) -> list[CohortPrior]:
        """Compute feature-level priors from the tenant's matching cohorts.

        For each matching cohort, aggregates per-feature-value reward
        statistics from cohort members (excluding the target tenant).
        Only returns priors that meet k-anonymity.
        """
        # Find matching cohorts
        matching = [d for d in definitions if d.is_active and d.matches(tenant_profile)]
        if not matching:
            return []

        priors: list[CohortPrior] = []

        for defn in matching:
            # Get cohort members excluding the target tenant
            members = [
                p for p in all_profiles
                if p.tenant_id != tenant_profile.tenant_id and defn.matches(p)
            ]
            tenant_ids = {p.tenant_id for p in members}
            if len(tenant_ids) < self.k:
                continue

            # Aggregate per-feature-value rewards
            feature_buckets: dict[str, dict[str, list[tuple[uuid.UUID, float]]]] = {}
            for p in members:
                for feat_name, feat_value in p.traits.items():
                    if feature_names and feat_name not in feature_names:
                        continue
                    # Skip non-scalar traits (lists, dicts)
                    if isinstance(feat_value, (list, dict)):
                        continue
                    str_value = str(feat_value)
                    feature_buckets.setdefault(feat_name, {})
                    feature_buckets[feat_name].setdefault(str_value, [])
                    for r in p.rewards:
                        feature_buckets[feat_name][str_value].append((p.tenant_id, r))

            for feat_name, value_buckets in feature_buckets.items():
                for feat_value, entries in value_buckets.items():
                    tenant_set = {tid for tid, _ in entries}
                    if len(tenant_set) < self.k:
                        continue
                    rewards = [r for _, r in entries]
                    mean = sum(rewards) / len(rewards)
                    std = _std_dev(rewards) if len(rewards) > 1 else None

                    priors.append(CohortPrior(
                        feature_name=feat_name,
                        feature_value=feat_value,
                        mean_reward=mean,
                        observation_count=len(rewards),
                        tenant_count=len(tenant_set),
                        std_dev=std,
                        confidence_lower=(
                            mean - 1.96 * (std / math.sqrt(len(rewards))) if std else None
                        ),
                        confidence_upper=(
                            mean + 1.96 * (std / math.sqrt(len(rewards))) if std else None
                        ),
                        cohort_id=defn.id,
                        cohort_name=defn.name,
                    ))

        return sorted(priors, key=lambda p: p.mean_reward, reverse=True)

    def blend_cold_start(
        self,
        tenant_reward: float | None,
        cohort_prior_reward: float,
        tenant_observations: int,
        cold_start_threshold: int = 20,
    ) -> float:
        """Blend tenant reward with cohort prior based on data volume.

        Returns a weighted blend that ramps from 100% cohort prior
        (when tenant has 0 observations) to 70% tenant weight
        (when tenant has >= cold_start_threshold observations).
        """
        max_tenant_weight = 0.7
        if tenant_reward is None or tenant_observations == 0:
            return cohort_prior_reward

        if tenant_observations >= cold_start_threshold:
            tenant_w = max_tenant_weight
        else:
            tenant_w = (tenant_observations / cold_start_threshold) * max_tenant_weight

        cohort_w = 1.0 - tenant_w
        return tenant_reward * tenant_w + cohort_prior_reward * cohort_w


# ── Matching helpers ──────────────────────────────────────────────


def _matches_rule(value: Any, rule: Any) -> bool:
    """Check if a profile trait value matches a criteria rule."""
    if value is None:
        return False

    # List rule → value must be in the list
    if isinstance(rule, list):
        return value in rule

    # Dict rule → complex matching
    if isinstance(rule, dict):
        if "includes_any" in rule:
            if not isinstance(value, list):
                return False
            return bool(set(value) & set(rule["includes_any"]))
        if "includes_all" in rule:
            if not isinstance(value, list):
                return False
            return set(rule["includes_all"]).issubset(set(value))
        if "min" in rule or "max" in rule:
            if not isinstance(value, (int, float)):
                return False
            if "min" in rule and value < rule["min"]:
                return False
            if "max" in rule and value > rule["max"]:
                return False
            return True
        return False

    # Scalar rule → exact match
    return value == rule


# ── Helpers ───────────────────────────────────────────────────────


def _std_dev(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    return math.sqrt(variance)


# ── Sample cohort definitions ─────────────────────────────────────


SAMPLE_COHORT_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "Hip-Hop & R&B Artists",
        "description": "Artists in the hip-hop/R&B genre family",
        "criteria": {
            "genre_family": ["hip-hop", "r-and-b", "rap", "trap"],
        },
    },
    {
        "name": "Pop & Indie Pop Artists",
        "description": "Artists in the pop/indie genre family",
        "criteria": {
            "genre_family": ["pop", "indie-pop", "synth-pop", "alt-pop"],
        },
    },
    {
        "name": "Electronic & Dance Artists",
        "description": "Artists in the electronic/dance genre family",
        "criteria": {
            "genre_family": ["electronic", "house", "techno", "edm", "drum-and-bass"],
        },
    },
    {
        "name": "Small Audience (< 10K)",
        "description": "Tenants with a small but growing audience",
        "criteria": {
            "audience_size_bucket": ["tiny", "small"],
        },
    },
    {
        "name": "Medium Audience (10K-100K)",
        "description": "Tenants with a medium-sized audience",
        "criteria": {
            "audience_size_bucket": "medium",
        },
    },
    {
        "name": "Large Audience (100K+)",
        "description": "Tenants with a large established audience",
        "criteria": {
            "audience_size_bucket": ["large", "huge"],
        },
    },
    {
        "name": "Short-Form Video Focus",
        "description": "Tenants whose content is primarily short-form video (Reels, TikTok)",
        "criteria": {
            "content_styles": {"includes_any": ["short-form-video", "reel", "tiktok"]},
        },
    },
    {
        "name": "Single Release Cycle",
        "description": "Tenants in a singles-driven release strategy",
        "criteria": {
            "release_type": "single",
        },
    },
    {
        "name": "Multi-Platform (IG + TikTok)",
        "description": "Tenants active on both Instagram and TikTok",
        "criteria": {
            "channel_mix": {"includes_all": ["instagram", "tiktok"]},
        },
    },
    {
        "name": "High Engagement Artists",
        "description": "Tenants with above-average engagement rates",
        "criteria": {
            "avg_engagement_rate": {"min": 0.05},
        },
    },
]

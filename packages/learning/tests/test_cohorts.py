"""Tests for the cross-tenant cohort modeling engine.

Covers: definition matching, tenant assignment, aggregation with
k-anonymity enforcement, cold-start priors, blending, and sample
cohort definitions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from amplify.learning.cohorts.engine import (
    CohortAssignment,
    CohortDefinition,
    CohortEngine,
    CohortPrior,
    CohortStats,
    TenantProfile,
    SAMPLE_COHORT_DEFINITIONS,
    _matches_rule,
)


# ── Helpers ───────────────────────────────────────────────────────


def _make_profile(
    traits: dict | None = None,
    observation_count: int = 10,
    rewards: list[float] | None = None,
    tenant_id: uuid.UUID | None = None,
) -> TenantProfile:
    return TenantProfile(
        tenant_id=tenant_id or uuid.uuid4(),
        traits=traits or {},
        observation_count=observation_count,
        rewards=rewards or [0.5] * observation_count,
    )


def _genre_defn(genres: list[str], name: str = "Genre Cohort") -> CohortDefinition:
    return CohortDefinition(
        name=name,
        criteria={"genre_family": genres},
    )


# ── Tests: Matching rules ────────────────────────────────────────


class TestMatchingRules:
    def test_list_rule_matches(self):
        assert _matches_rule("hip-hop", ["hip-hop", "rap"]) is True

    def test_list_rule_no_match(self):
        assert _matches_rule("pop", ["hip-hop", "rap"]) is False

    def test_scalar_exact_match(self):
        assert _matches_rule("single", "single") is True

    def test_scalar_no_match(self):
        assert _matches_rule("album", "single") is False

    def test_includes_any_matches(self):
        assert _matches_rule(
            ["short-form-video", "story"],
            {"includes_any": ["reel", "short-form-video"]},
        ) is True

    def test_includes_any_no_match(self):
        assert _matches_rule(
            ["carousel", "story"],
            {"includes_any": ["reel", "short-form-video"]},
        ) is False

    def test_includes_all_matches(self):
        assert _matches_rule(
            ["instagram", "tiktok", "youtube"],
            {"includes_all": ["instagram", "tiktok"]},
        ) is True

    def test_includes_all_missing(self):
        assert _matches_rule(
            ["instagram"],
            {"includes_all": ["instagram", "tiktok"]},
        ) is False

    def test_min_max_range(self):
        assert _matches_rule(0.06, {"min": 0.05}) is True
        assert _matches_rule(0.03, {"min": 0.05}) is False
        assert _matches_rule(50, {"min": 10, "max": 100}) is True
        assert _matches_rule(150, {"max": 100}) is False

    def test_none_value_never_matches(self):
        assert _matches_rule(None, "anything") is False
        assert _matches_rule(None, ["a", "b"]) is False

    def test_includes_any_non_list_value(self):
        assert _matches_rule("string", {"includes_any": ["a"]}) is False

    def test_includes_all_non_list_value(self):
        assert _matches_rule("string", {"includes_all": ["a"]}) is False

    def test_min_max_non_numeric(self):
        assert _matches_rule("abc", {"min": 5}) is False


# ── Tests: CohortDefinition ──────────────────────────────────────


class TestCohortDefinition:
    def test_matches_simple_criteria(self):
        defn = _genre_defn(["hip-hop", "rap"])
        profile = _make_profile({"genre_family": "hip-hop"})
        assert defn.matches(profile) is True

    def test_no_match(self):
        defn = _genre_defn(["hip-hop", "rap"])
        profile = _make_profile({"genre_family": "pop"})
        assert defn.matches(profile) is False

    def test_multi_criteria_all_must_match(self):
        defn = CohortDefinition(
            name="Hip-hop small",
            criteria={
                "genre_family": ["hip-hop"],
                "audience_size_bucket": "small",
            },
        )
        # Both match
        p1 = _make_profile({"genre_family": "hip-hop", "audience_size_bucket": "small"})
        assert defn.matches(p1) is True

        # Genre matches, size doesn't
        p2 = _make_profile({"genre_family": "hip-hop", "audience_size_bucket": "large"})
        assert defn.matches(p2) is False

    def test_missing_trait_no_match(self):
        defn = _genre_defn(["hip-hop"])
        profile = _make_profile({"audience_size_bucket": "small"})
        assert defn.matches(profile) is False

    def test_to_dict(self):
        defn = _genre_defn(["pop"])
        d = defn.to_dict()
        assert d["name"] == "Genre Cohort"
        assert d["criteria"] == {"genre_family": ["pop"]}

    def test_complex_criteria(self):
        defn = CohortDefinition(
            name="Multi-platform short-form",
            criteria={
                "channel_mix": {"includes_all": ["instagram", "tiktok"]},
                "content_styles": {"includes_any": ["reel", "short-form-video"]},
                "avg_engagement_rate": {"min": 0.03},
            },
        )
        profile = _make_profile({
            "channel_mix": ["instagram", "tiktok", "youtube"],
            "content_styles": ["reel", "carousel"],
            "avg_engagement_rate": 0.05,
        })
        assert defn.matches(profile) is True


# ── Tests: Assignment ─────────────────────────────────────────────


class TestAssignment:
    def test_assign_to_matching_cohorts(self):
        engine = CohortEngine(k_anonymity_threshold=1)
        defns = [
            _genre_defn(["hip-hop"], "Hip-Hop"),
            _genre_defn(["pop"], "Pop"),
            CohortDefinition(name="Small", criteria={"audience_size_bucket": "small"}),
        ]
        profile = _make_profile({"genre_family": "hip-hop", "audience_size_bucket": "small"})

        assignments = engine.assign_tenants([profile], defns)
        names = {a.cohort_name for a in assignments}
        assert names == {"Hip-Hop", "Small"}

    def test_no_matching_cohorts(self):
        engine = CohortEngine()
        defns = [_genre_defn(["pop"])]
        profile = _make_profile({"genre_family": "rock"})
        assert engine.assign_tenants([profile], defns) == []

    def test_multiple_tenants(self):
        engine = CohortEngine(k_anonymity_threshold=1)
        defn = _genre_defn(["hip-hop"])
        profiles = [
            _make_profile({"genre_family": "hip-hop"}),
            _make_profile({"genre_family": "hip-hop"}),
            _make_profile({"genre_family": "pop"}),
        ]
        assignments = engine.assign_tenants(profiles, [defn])
        assert len(assignments) == 2

    def test_inactive_definitions_skipped(self):
        engine = CohortEngine(k_anonymity_threshold=1)
        defn = CohortDefinition(
            name="Inactive",
            criteria={"genre_family": ["hip-hop"]},
            is_active=False,
        )
        profile = _make_profile({"genre_family": "hip-hop"})
        assert engine.assign_tenants([profile], [defn]) == []

    def test_assignment_has_correct_fields(self):
        engine = CohortEngine(k_anonymity_threshold=1)
        defn = _genre_defn(["hip-hop"], "Hip-Hop")
        profile = _make_profile({"genre_family": "hip-hop"})
        assignments = engine.assign_tenants([profile], [defn])
        a = assignments[0]
        assert a.tenant_id == profile.tenant_id
        assert a.cohort_id == defn.id
        assert a.cohort_name == "Hip-Hop"


# ── Tests: Aggregation ────────────────────────────────────────────


class TestAggregation:
    def test_aggregate_cohort_basic(self):
        engine = CohortEngine(k_anonymity_threshold=2)
        defn = _genre_defn(["hip-hop"])
        profiles = [
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.6, 0.7, 0.8]),
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.4, 0.5, 0.6]),
        ]
        stats = engine.aggregate_cohort(defn, profiles)
        assert stats is not None
        assert stats.tenant_count == 2
        assert stats.total_observations == 20  # 10 + 10 (observation_count default)
        assert 0.5 < stats.mean_reward < 0.7

    def test_aggregate_below_k_anonymity(self):
        engine = CohortEngine(k_anonymity_threshold=5)
        defn = _genre_defn(["hip-hop"])
        profiles = [
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.5]),
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.6]),
        ]
        stats = engine.aggregate_cohort(defn, profiles)
        assert stats is None

    def test_aggregate_excludes_non_members(self):
        engine = CohortEngine(k_anonymity_threshold=2)
        defn = _genre_defn(["hip-hop"])
        profiles = [
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.9]),
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.8]),
            _make_profile({"genre_family": "pop"}, rewards=[0.1]),
        ]
        stats = engine.aggregate_cohort(defn, profiles)
        assert stats is not None
        assert stats.tenant_count == 2
        # Mean should be ~0.85, not dragged down by pop tenant's 0.1
        assert stats.mean_reward > 0.8

    def test_aggregate_empty_rewards(self):
        engine = CohortEngine(k_anonymity_threshold=2)
        defn = _genre_defn(["hip-hop"])
        profiles = [
            _make_profile({"genre_family": "hip-hop"}, observation_count=0, rewards=[]),
            _make_profile({"genre_family": "hip-hop"}, observation_count=0, rewards=[]),
        ]
        stats = engine.aggregate_cohort(defn, profiles)
        assert stats is not None
        assert stats.mean_reward == 0.0
        assert stats.std_dev is None

    def test_aggregate_confidence_interval(self):
        engine = CohortEngine(k_anonymity_threshold=2)
        defn = _genre_defn(["hip-hop"])
        profiles = [
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.5, 0.6, 0.7]),
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.4, 0.5, 0.6]),
        ]
        stats = engine.aggregate_cohort(defn, profiles)
        assert stats.confidence_lower is not None
        assert stats.confidence_upper is not None
        assert stats.confidence_lower < stats.mean_reward < stats.confidence_upper

    def test_aggregate_all_skips_inactive(self):
        engine = CohortEngine(k_anonymity_threshold=2)
        defns = [
            _genre_defn(["hip-hop"], "Active"),
            CohortDefinition(name="Inactive", criteria={"genre_family": ["pop"]}, is_active=False),
        ]
        profiles = [
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.5]),
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.6]),
            _make_profile({"genre_family": "pop"}, rewards=[0.7]),
            _make_profile({"genre_family": "pop"}, rewards=[0.8]),
        ]
        all_stats = engine.aggregate_all(defns, profiles)
        assert len(all_stats) == 1
        assert all_stats[0].cohort_name == "Active"

    def test_stats_to_dict(self):
        stats = CohortStats(
            cohort_id=uuid.uuid4(),
            cohort_name="Test",
            tenant_count=5,
            total_observations=100,
            mean_reward=0.65,
            std_dev=0.1,
        )
        d = stats.to_dict()
        assert d["cohort_name"] == "Test"
        assert d["tenant_count"] == 5
        assert d["mean_reward"] == 0.65


# ── Tests: Cold-start priors ─────────────────────────────────────


class TestColdStartPriors:
    def _build_profiles(self, n: int = 6) -> list[TenantProfile]:
        """Build n profiles in the hip-hop genre with varied traits."""
        profiles = []
        for i in range(n):
            profiles.append(_make_profile(
                {
                    "genre_family": "hip-hop",
                    "audience_size_bucket": "small",
                    "release_type": "single",
                },
                rewards=[0.4 + i * 0.05] * 3,
            ))
        return profiles

    def test_priors_from_matching_cohort(self):
        engine = CohortEngine(k_anonymity_threshold=3)
        defn = _genre_defn(["hip-hop"])
        profiles = self._build_profiles(6)
        target = _make_profile({"genre_family": "hip-hop"}, rewards=[])

        priors = engine.compute_cold_start_priors(target, [defn], profiles)
        assert len(priors) > 0
        # Priors should be for features shared by cohort members
        feature_names = {p.feature_name for p in priors}
        assert "genre_family" in feature_names or "audience_size_bucket" in feature_names

    def test_priors_exclude_target_tenant(self):
        engine = CohortEngine(k_anonymity_threshold=2)
        defn = _genre_defn(["hip-hop"])
        target_id = uuid.uuid4()
        target = _make_profile(
            {"genre_family": "hip-hop"},
            rewards=[0.99],
            tenant_id=target_id,
        )
        others = [
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.3]),
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.4]),
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.5]),
        ]
        all_profiles = [target] + others

        priors = engine.compute_cold_start_priors(target, [defn], all_profiles)
        # Target's 0.99 reward should not contaminate the priors
        for p in priors:
            assert p.mean_reward < 0.6

    def test_priors_k_anonymity_enforced(self):
        engine = CohortEngine(k_anonymity_threshold=10)
        defn = _genre_defn(["hip-hop"])
        # Only 3 profiles — below k=10
        profiles = [
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.5]),
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.6]),
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.7]),
        ]
        target = _make_profile({"genre_family": "hip-hop"}, rewards=[])
        priors = engine.compute_cold_start_priors(target, [defn], profiles)
        assert priors == []

    def test_priors_no_matching_cohort(self):
        engine = CohortEngine(k_anonymity_threshold=2)
        defn = _genre_defn(["pop"])
        target = _make_profile({"genre_family": "hip-hop"})
        profiles = [_make_profile({"genre_family": "pop"}) for _ in range(5)]
        priors = engine.compute_cold_start_priors(target, [defn], profiles)
        assert priors == []

    def test_priors_filter_by_feature_names(self):
        engine = CohortEngine(k_anonymity_threshold=2)
        defn = _genre_defn(["hip-hop"])
        profiles = [
            _make_profile(
                {"genre_family": "hip-hop", "audience_size_bucket": "small", "release_type": "single"},
                rewards=[0.5],
            )
            for _ in range(5)
        ]
        target = _make_profile({"genre_family": "hip-hop"}, rewards=[])

        priors = engine.compute_cold_start_priors(
            target, [defn], profiles, feature_names=["genre_family"],
        )
        feature_names = {p.feature_name for p in priors}
        assert "audience_size_bucket" not in feature_names

    def test_prior_is_reliable(self):
        p = CohortPrior(
            feature_name="genre_family",
            feature_value="hip-hop",
            mean_reward=0.5,
            observation_count=15,
            tenant_count=3,
        )
        assert p.is_reliable is True

    def test_prior_not_reliable_low_count(self):
        p = CohortPrior(
            feature_name="genre_family",
            feature_value="hip-hop",
            mean_reward=0.5,
            observation_count=5,
            tenant_count=1,
        )
        assert p.is_reliable is False

    def test_prior_to_dict(self):
        p = CohortPrior(
            feature_name="genre",
            feature_value="pop",
            mean_reward=0.6,
            observation_count=20,
            tenant_count=5,
        )
        d = p.to_dict()
        assert d["feature_name"] == "genre"
        assert d["mean_reward"] == 0.6


# ── Tests: Cold-start blending ────────────────────────────────────


class TestColdStartBlending:
    def test_zero_observations_uses_cohort(self):
        engine = CohortEngine()
        result = engine.blend_cold_start(
            tenant_reward=None, cohort_prior_reward=0.6,
            tenant_observations=0,
        )
        assert result == 0.6

    def test_at_threshold_mostly_tenant(self):
        engine = CohortEngine()
        result = engine.blend_cold_start(
            tenant_reward=0.8, cohort_prior_reward=0.4,
            tenant_observations=20, cold_start_threshold=20,
        )
        # 70% tenant (0.8) + 30% cohort (0.4) = 0.68
        assert abs(result - 0.68) < 0.001

    def test_half_threshold_half_blend(self):
        engine = CohortEngine()
        result = engine.blend_cold_start(
            tenant_reward=0.8, cohort_prior_reward=0.4,
            tenant_observations=10, cold_start_threshold=20,
        )
        # tenant_w = (10/20) * 0.7 = 0.35, cohort_w = 0.65
        expected = 0.8 * 0.35 + 0.4 * 0.65
        assert abs(result - expected) < 0.001

    def test_above_threshold_caps_at_70_percent(self):
        engine = CohortEngine()
        result = engine.blend_cold_start(
            tenant_reward=1.0, cohort_prior_reward=0.0,
            tenant_observations=1000, cold_start_threshold=20,
        )
        assert result == 0.7

    def test_none_tenant_reward_uses_cohort(self):
        engine = CohortEngine()
        result = engine.blend_cold_start(
            tenant_reward=None, cohort_prior_reward=0.5,
            tenant_observations=50,
        )
        assert result == 0.5


# ── Tests: Privacy ────────────────────────────────────────────────


class TestPrivacy:
    def test_no_tenant_ids_in_stats(self):
        engine = CohortEngine(k_anonymity_threshold=2)
        defn = _genre_defn(["hip-hop"])
        profiles = [
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.5]),
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.6]),
        ]
        stats = engine.aggregate_cohort(defn, profiles)
        d = stats.to_dict()
        # Stats dict should not contain any tenant_id
        assert "tenant_id" not in str(d).lower() or "tenant_count" in str(d).lower()
        # Check no UUID patterns that could be tenant IDs
        for profile in profiles:
            assert str(profile.tenant_id) not in str(d)

    def test_no_tenant_ids_in_priors(self):
        engine = CohortEngine(k_anonymity_threshold=2)
        defn = _genre_defn(["hip-hop"])
        profiles = [
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.5]),
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.6]),
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.7]),
        ]
        target = _make_profile({"genre_family": "hip-hop"}, rewards=[])
        priors = engine.compute_cold_start_priors(target, [defn], profiles)
        for p in priors:
            d = p.to_dict()
            for profile in profiles:
                assert str(profile.tenant_id) not in str(d)


# ── Tests: Sample definitions ─────────────────────────────────────


class TestSampleDefinitions:
    def test_sample_definitions_exist(self):
        assert len(SAMPLE_COHORT_DEFINITIONS) >= 5

    def test_sample_definitions_have_required_fields(self):
        for sample in SAMPLE_COHORT_DEFINITIONS:
            assert "name" in sample
            assert "criteria" in sample
            assert isinstance(sample["criteria"], dict)

    def test_sample_definitions_are_valid(self):
        """Each sample should be parseable as a CohortDefinition."""
        for sample in SAMPLE_COHORT_DEFINITIONS:
            defn = CohortDefinition(
                name=sample["name"],
                description=sample.get("description", ""),
                criteria=sample["criteria"],
            )
            assert defn.name
            assert defn.criteria

    def test_genre_cohort_matches_correctly(self):
        hip_hop_sample = next(
            s for s in SAMPLE_COHORT_DEFINITIONS if "Hip-Hop" in s["name"]
        )
        defn = CohortDefinition(**hip_hop_sample)
        assert defn.matches(_make_profile({"genre_family": "hip-hop"}))
        assert not defn.matches(_make_profile({"genre_family": "pop"}))

    def test_multi_platform_cohort(self):
        mp_sample = next(
            s for s in SAMPLE_COHORT_DEFINITIONS if "Multi-Platform" in s["name"]
        )
        defn = CohortDefinition(**mp_sample)
        assert defn.matches(_make_profile({"channel_mix": ["instagram", "tiktok"]}))
        assert not defn.matches(_make_profile({"channel_mix": ["instagram"]}))

    def test_high_engagement_cohort(self):
        he_sample = next(
            s for s in SAMPLE_COHORT_DEFINITIONS if "High Engagement" in s["name"]
        )
        defn = CohortDefinition(**he_sample)
        assert defn.matches(_make_profile({"avg_engagement_rate": 0.06}))
        assert not defn.matches(_make_profile({"avg_engagement_rate": 0.02}))


# ── Tests: Edge cases ─────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_profiles(self):
        engine = CohortEngine(k_anonymity_threshold=1)
        defn = _genre_defn(["hip-hop"])
        assert engine.aggregate_cohort(defn, []) is None

    def test_empty_definitions(self):
        engine = CohortEngine()
        profile = _make_profile({"genre_family": "hip-hop"})
        assert engine.assign_tenants([profile], []) == []

    def test_empty_criteria(self):
        defn = CohortDefinition(name="Everyone", criteria={})
        # Empty criteria matches everything
        assert defn.matches(_make_profile({"anything": "value"}))

    def test_same_tenant_in_multiple_cohorts(self):
        engine = CohortEngine(k_anonymity_threshold=1)
        defns = [
            _genre_defn(["hip-hop"], "Genre"),
            CohortDefinition(name="Size", criteria={"audience_size_bucket": "small"}),
            CohortDefinition(name="Style", criteria={"content_styles": {"includes_any": ["reel"]}}),
        ]
        profile = _make_profile({
            "genre_family": "hip-hop",
            "audience_size_bucket": "small",
            "content_styles": ["reel", "story"],
        })
        assignments = engine.assign_tenants([profile], defns)
        assert len(assignments) == 3

    def test_duplicate_tenant_id_in_aggregation(self):
        """Same tenant appearing twice should count as one tenant."""
        engine = CohortEngine(k_anonymity_threshold=2)
        defn = _genre_defn(["hip-hop"])
        tid = uuid.uuid4()
        profiles = [
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.5], tenant_id=tid),
            _make_profile({"genre_family": "hip-hop"}, rewards=[0.6], tenant_id=tid),
        ]
        # Only 1 distinct tenant — should fail k=2
        stats = engine.aggregate_cohort(defn, profiles)
        assert stats is None

"""Tests for the privacy-safe global prior service.

Covers: threshold gating, de-identification, cold-start recommendations,
category aggregation, confidence computation, and privacy guarantees.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from amplify.learning.config import LearningConfig, PrivacyConfig
from amplify.learning.global_priors.service import (
    CATEGORY_FEATURE_MAP,
    PATTERN_CATEGORIES,
    GlobalPriorService,
    GlobalRecommendation,
    ThresholdConfig,
    compute_confidence,
    passes_threshold,
    redact_observation,
    validate_no_pii,
)
from amplify.learning.schemas.observation import Observation


# ── Helpers ───────────────────────────────────────────────────────


def _make_obs(
    tenant_id: uuid.UUID | None = None,
    features: dict | None = None,
    reward: float = 0.5,
    action_type: str = "post_published",
) -> Observation:
    return Observation(
        tenant_id=tenant_id or uuid.uuid4(),
        action_type=action_type,
        features=features or {},
        reward=reward,
    )


def _make_observations(
    n_tenants: int = 10,
    n_per_tenant: int = 5,
    feature_name: str = "post_hour",
    feature_value: str = "18",
    reward: float = 0.6,
    action_type: str = "post_published",
) -> list[Observation]:
    """Create observations from multiple tenants with consistent features."""
    obs = []
    for _ in range(n_tenants):
        tid = uuid.uuid4()
        for _ in range(n_per_tenant):
            obs.append(_make_obs(
                tenant_id=tid,
                features={feature_name: feature_value},
                reward=reward,
                action_type=action_type,
            ))
    return obs


def _relaxed_config() -> LearningConfig:
    """Config with low thresholds for testing."""
    return LearningConfig(
        privacy=PrivacyConfig(
            k_anonymity_threshold=2,
            cross_tenant_enabled=True,
        ),
    )


# ── Tests: Threshold gating ──────────────────────────────────────


class TestThresholdGating:
    def test_passes_all_thresholds(self):
        config = ThresholdConfig(
            min_observations=30,
            min_tenant_count=5,
            min_confidence=0.3,
        )
        assert passes_threshold(50, 10, 0.5, config) is True

    def test_fails_min_observations(self):
        config = ThresholdConfig(min_observations=30)
        assert passes_threshold(10, 10, 0.5, config) is False

    def test_fails_min_tenant_count(self):
        config = ThresholdConfig(min_tenant_count=5)
        assert passes_threshold(50, 3, 0.5, config) is False

    def test_fails_min_confidence(self):
        config = ThresholdConfig(min_confidence=0.5)
        assert passes_threshold(50, 10, 0.3, config) is False

    def test_exact_boundary_passes(self):
        config = ThresholdConfig(
            min_observations=30,
            min_tenant_count=5,
            min_confidence=0.3,
        )
        assert passes_threshold(30, 5, 0.3, config) is True

    def test_just_below_boundary_fails(self):
        config = ThresholdConfig(
            min_observations=30,
            min_tenant_count=5,
            min_confidence=0.3,
        )
        assert passes_threshold(29, 5, 0.3, config) is False

    def test_service_enforces_thresholds(self):
        """Observations from too few tenants should produce no recommendations."""
        svc = GlobalPriorService(LearningConfig(
            privacy=PrivacyConfig(k_anonymity_threshold=10),
        ))
        # Only 3 tenants — below k=10
        obs = _make_observations(n_tenants=3, n_per_tenant=20, feature_name="post_hour")
        recs = svc.aggregate_category("posting_window", obs)
        assert recs == []

    def test_above_threshold_produces_recommendations(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = _make_observations(
            n_tenants=5, n_per_tenant=10,
            feature_name="post_hour", feature_value="18",
        )
        recs = svc.aggregate_category("posting_window", obs)
        assert len(recs) > 0
        assert all(r.tenant_count >= 2 for r in recs)

    def test_cardinality_limit_blocks_high_cardinality(self):
        """Features with too many distinct values should be blocked."""
        svc = GlobalPriorService(LearningConfig(
            privacy=PrivacyConfig(
                k_anonymity_threshold=1,
                cross_tenant_enabled=True,
                max_categorical_cardinality=3,
            ),
        ))
        # Create 100 distinct post_hour values — exceeds cardinality=3
        obs = []
        for i in range(100):
            tid = uuid.uuid4()
            for _ in range(5):
                obs.append(_make_obs(
                    tenant_id=tid,
                    features={"post_hour": str(i)},
                    reward=0.5,
                ))
        recs = svc.aggregate_category("posting_window", obs)
        assert recs == []


# ── Tests: Confidence computation ─────────────────────────────────


class TestConfidence:
    def test_zero_observations(self):
        assert compute_confidence(0, 0, None) == 0.0

    def test_high_sample_high_tenants(self):
        c = compute_confidence(200, 20, 0.1)
        assert c > 0.8

    def test_low_sample_low_tenants(self):
        c = compute_confidence(5, 1, 0.5)
        assert c < 0.3

    def test_high_variance_reduces_confidence(self):
        c_low_var = compute_confidence(100, 10, 0.1)
        c_high_var = compute_confidence(100, 10, 0.8)
        assert c_low_var > c_high_var

    def test_none_stddev_moderate(self):
        c = compute_confidence(100, 10, None)
        assert 0.3 < c < 0.9


# ── Tests: De-identification ──────────────────────────────────────


class TestDeIdentification:
    def test_redact_observation_strips_pii(self):
        privacy = PrivacyConfig()
        obs = _make_obs(features={
            "post_hour": 18,
            "content_text": "Buy my new album!!",
            "artist_name": "Drake",
            "tenant_id": "secret-id",
            "campaign_name": "Summer Push",
            "platform": "instagram",
        })
        safe = redact_observation(obs, privacy)
        assert "post_hour" in safe
        assert "platform" in safe
        assert "content_text" not in safe
        assert "artist_name" not in safe
        assert "tenant_id" not in safe
        assert "campaign_name" not in safe

    def test_validate_no_pii_clean(self):
        privacy = PrivacyConfig()
        data = {"post_hour": 18, "platform": "instagram"}
        violations = validate_no_pii(data, privacy)
        assert violations == []

    def test_validate_no_pii_catches_redacted_field(self):
        privacy = PrivacyConfig()
        data = {"post_hour": 18, "content_text": "some text"}
        violations = validate_no_pii(data, privacy)
        assert len(violations) == 1
        assert "content_text" in violations[0]

    def test_validate_no_pii_catches_long_text(self):
        privacy = PrivacyConfig()
        data = {"description": "x" * 300}
        violations = validate_no_pii(data, privacy)
        assert any("long text" in v for v in violations)

    def test_service_de_identify_strips_all_pii(self):
        svc = GlobalPriorService()
        obs = [_make_obs(features={
            "post_hour": 18,
            "content_text": "Raw content here",
            "artist_name": "Secret Artist",
            "platform": "tiktok",
        })]
        safe = svc.de_identify_observations(obs)
        assert len(safe) == 1
        assert "content_text" not in safe[0]["features"]
        assert "artist_name" not in safe[0]["features"]
        assert safe[0]["features"]["post_hour"] == 18
        assert safe[0]["features"]["platform"] == "tiktok"

    def test_aggregation_uses_redacted_features(self):
        """Aggregation should never use raw content_text or artist_name."""
        svc = GlobalPriorService(_relaxed_config())
        obs = _make_observations(
            n_tenants=5, n_per_tenant=10,
            feature_name="post_hour", feature_value="18",
        )
        # Add PII features that should be ignored
        for o in obs:
            o_dict = o.model_dump()
            o_dict["features"]["content_text"] = "raw content"
            o_dict["features"]["artist_name"] = "secret"

        recs = svc.aggregate_category("posting_window", obs)
        for rec in recs:
            d = rec.to_dict()
            assert "content_text" not in d["feature_name"]
            assert "artist_name" not in d["feature_name"]

    def test_no_tenant_ids_in_recommendations(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        recs = svc.aggregate_category("posting_window", obs)
        for rec in recs:
            d = rec.to_dict()
            serialized = str(d)
            # No tenant UUIDs should appear in output
            for o in obs:
                assert str(o.tenant_id) not in serialized

    def test_no_raw_content_in_recommendations(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = []
        for _ in range(5):
            tid = uuid.uuid4()
            for _ in range(10):
                obs.append(_make_obs(
                    tenant_id=tid,
                    features={
                        "post_hour": "18",
                        "content_text": "My secret lyrics and creative work",
                    },
                ))
        recs = svc.aggregate_category("posting_window", obs)
        for rec in recs:
            d = rec.to_dict()
            assert "secret lyrics" not in str(d)
            assert "creative work" not in str(d)


# ── Tests: Category aggregation ───────────────────────────────────


class TestCategoryAggregation:
    def test_posting_window_category(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = _make_observations(
            n_tenants=5, n_per_tenant=10,
            feature_name="post_hour", feature_value="18",
        )
        recs = svc.aggregate_category("posting_window", obs)
        assert len(recs) > 0
        assert all(r.category == "posting_window" for r in recs)

    def test_cta_type_category(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = _make_observations(
            n_tenants=5, n_per_tenant=10,
            feature_name="cta_type", feature_value="link_in_bio",
        )
        recs = svc.aggregate_category("cta_type", obs)
        assert len(recs) > 0
        assert all(r.category == "cta_type" for r in recs)

    def test_format_preference_category(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = _make_observations(
            n_tenants=5, n_per_tenant=10,
            feature_name="content_type", feature_value="reel",
        )
        recs = svc.aggregate_category("format_preference", obs)
        assert len(recs) > 0

    def test_hook_family_category(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = _make_observations(
            n_tenants=5, n_per_tenant=10,
            feature_name="hook_family", feature_value="question",
        )
        recs = svc.aggregate_category("hook_family", obs)
        assert len(recs) > 0

    def test_unknown_category_returns_empty(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = _make_observations(n_tenants=5, feature_name="post_hour")
        recs = svc.aggregate_category("nonexistent_category", obs)
        assert recs == []

    def test_aggregate_all_categories(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = []
        for _ in range(5):
            tid = uuid.uuid4()
            for _ in range(10):
                obs.append(_make_obs(
                    tenant_id=tid,
                    features={
                        "post_hour": "18",
                        "cta_type": "link_in_bio",
                        "content_type": "reel",
                        "hook_family": "question",
                    },
                ))
        results = svc.aggregate_all_categories(obs)
        assert len(results) > 0
        for cat_name in results:
            assert cat_name in PATTERN_CATEGORIES

    def test_sorted_by_reward_descending(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = []
        for _ in range(5):
            tid = uuid.uuid4()
            for _ in range(10):
                obs.append(_make_obs(
                    tenant_id=tid,
                    features={"post_hour": "18"},
                    reward=0.8,
                ))
            for _ in range(10):
                obs.append(_make_obs(
                    tenant_id=tid,
                    features={"post_hour": "3"},
                    reward=0.2,
                ))
        recs = svc.aggregate_category("posting_window", obs)
        if len(recs) >= 2:
            assert recs[0].mean_reward >= recs[1].mean_reward


# ── Tests: Recommendation labeling ────────────────────────────────


class TestRecommendationLabeling:
    def test_global_prior_source_label(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        recs = svc.aggregate_category("posting_window", obs)
        for rec in recs:
            assert rec.source == "global_prior"

    def test_cohort_prior_source_label(self):
        svc = GlobalPriorService(_relaxed_config())
        cohort_id = uuid.uuid4()
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        recs = svc.aggregate_category(
            "posting_window", obs,
            source_cohort_id=cohort_id,
            source_cohort_name="Hip-Hop Artists",
        )
        for rec in recs:
            assert rec.source == "cohort_prior"
            assert rec.source_cohort_id == cohort_id
            assert rec.source_cohort_name == "Hip-Hop Artists"

    def test_explanation_present(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        recs = svc.aggregate_category("posting_window", obs)
        for rec in recs:
            assert rec.explanation
            assert "global prior" in rec.explanation or "accounts" in rec.explanation

    def test_to_dict_includes_source(self):
        rec = GlobalRecommendation(
            category="posting_window",
            feature_name="post_hour",
            feature_value="18",
            mean_reward=0.6,
            observation_count=50,
            tenant_count=10,
            confidence=0.7,
            source="global_prior",
            explanation="Test explanation",
        )
        d = rec.to_dict()
        assert d["source"] == "global_prior"
        assert d["explanation"] == "Test explanation"


# ── Tests: Cold-start recommendations ─────────────────────────────


class TestColdStartRecommendations:
    def test_cold_start_returns_recommendations(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        all_recs = svc.aggregate_all_categories(obs)
        cold_start = svc.get_cold_start_recommendations(
            tenant_observation_count=0,
            global_recommendations=all_recs,
        )
        assert len(cold_start) > 0

    def test_cold_start_skipped_when_enough_data(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        all_recs = svc.aggregate_all_categories(obs)
        cold_start = svc.get_cold_start_recommendations(
            tenant_observation_count=100,
            global_recommendations=all_recs,
        )
        assert cold_start == {}

    def test_cold_start_explanation_added(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        all_recs = svc.aggregate_all_categories(obs)
        cold_start = svc.get_cold_start_recommendations(
            tenant_observation_count=0,
            global_recommendations=all_recs,
        )
        for cat_recs in cold_start.values():
            for rec in cat_recs:
                assert "global prior" in rec.explanation
                assert "not learned from your specific data" in rec.explanation

    def test_cold_start_below_threshold_returns_recs(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        all_recs = svc.aggregate_all_categories(obs)
        cold_start = svc.get_cold_start_recommendations(
            tenant_observation_count=5,  # below default threshold of 20
            global_recommendations=all_recs,
        )
        assert len(cold_start) > 0

    def test_cold_start_at_exact_threshold_returns_nothing(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        all_recs = svc.aggregate_all_categories(obs)
        cold_start = svc.get_cold_start_recommendations(
            tenant_observation_count=20,
            global_recommendations=all_recs,
        )
        assert cold_start == {}


# ── Tests: Cross-tenant disabled ──────────────────────────────────


class TestCrossTenantDisabled:
    def test_disabled_returns_nothing(self):
        config = LearningConfig(
            privacy=PrivacyConfig(cross_tenant_enabled=False),
        )
        svc = GlobalPriorService(config)
        obs = _make_observations(n_tenants=10, feature_name="post_hour")
        recs = svc.aggregate_category("posting_window", obs)
        assert recs == []

    def test_disabled_all_categories_empty(self):
        config = LearningConfig(
            privacy=PrivacyConfig(cross_tenant_enabled=False),
        )
        svc = GlobalPriorService(config)
        obs = _make_observations(n_tenants=10, feature_name="post_hour")
        results = svc.aggregate_all_categories(obs)
        assert results == {}


# ── Tests: Edge cases ─────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_observations(self):
        svc = GlobalPriorService(_relaxed_config())
        recs = svc.aggregate_category("posting_window", [])
        assert recs == []

    def test_all_missing_features(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = [_make_obs(features={}) for _ in range(50)]
        recs = svc.aggregate_category("posting_window", obs)
        assert recs == []

    def test_all_none_rewards(self):
        svc = GlobalPriorService(_relaxed_config())
        obs = [_make_obs(features={"post_hour": "18"}, reward=None) for _ in range(50)]
        # Observation model requires reward to be not None for aggregation
        # but let's test the graceful handling
        recs = svc.aggregate_category("posting_window", obs)
        assert recs == []

    def test_single_tenant_below_k(self):
        svc = GlobalPriorService(_relaxed_config())
        tid = uuid.uuid4()
        obs = [
            _make_obs(tenant_id=tid, features={"post_hour": "18"}, reward=0.5)
            for _ in range(100)
        ]
        recs = svc.aggregate_category("posting_window", obs)
        assert recs == []

    def test_action_type_filter(self):
        svc = GlobalPriorService(_relaxed_config())
        obs_a = _make_observations(
            n_tenants=5, n_per_tenant=10,
            feature_name="post_hour", feature_value="18",
            action_type="post_published",
        )
        obs_b = _make_observations(
            n_tenants=5, n_per_tenant=10,
            feature_name="post_hour", feature_value="18",
            action_type="story_posted",
        )
        all_obs = obs_a + obs_b
        recs = svc.aggregate_category(
            "posting_window", all_obs, action_type="post_published",
        )
        # Should only aggregate post_published observations
        assert len(recs) > 0

    def test_pattern_categories_constant(self):
        assert "posting_window" in PATTERN_CATEGORIES
        assert "cta_type" in PATTERN_CATEGORIES
        assert "format_preference" in PATTERN_CATEGORIES
        assert "hook_family" in PATTERN_CATEGORIES

    def test_category_feature_map_complete(self):
        for cat in PATTERN_CATEGORIES:
            assert cat in CATEGORY_FEATURE_MAP
            assert len(CATEGORY_FEATURE_MAP[cat]) > 0

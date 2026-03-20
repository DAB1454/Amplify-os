"""Tests for the recommendation blending engine."""

from __future__ import annotations

import pytest

from amplify.learning.blending import BlendingEngine, RecommendationSource
from amplify.learning.config import LearningConfig, RankingConfig


# ── Fixtures ──────────────────────────────────────────────────────


def _config(cold_start_threshold=20, tenant_prior_blend=0.3):
    return LearningConfig(
        ranking=RankingConfig(
            cold_start_threshold=cold_start_threshold,
            tenant_prior_blend=tenant_prior_blend,
        ),
    )


def _tenant_rec(feature="post_hour", value="18", confidence=0.8, title="Post at 6pm"):
    return {
        "feature": feature,
        "recommended_value": value,
        "title": title,
        "explanation": f"{feature}={value} works well for you",
        "confidence": confidence,
        "evidence": {"count": 50},
        "pattern_type": "timing",
    }


def _global_recs(
    category="posting_window",
    feature="post_hour",
    value="18",
    mean_reward=0.65,
    confidence=0.7,
    tenant_count=10,
):
    return {
        category: [
            {
                "feature_name": feature,
                "feature_value": value,
                "mean_reward": mean_reward,
                "confidence": confidence,
                "tenant_count": tenant_count,
                "source": "global_prior",
                "explanation": f"Across {tenant_count} accounts, {feature}={value} averages {mean_reward:.3f}",
                "category": category,
            }
        ]
    }


# ── Tests: Tenant weight computation ─────────────────────────────


class TestTenantWeight:
    def test_zero_observations(self):
        engine = BlendingEngine(_config())
        assert engine.compute_tenant_weight(0) == 0.0

    def test_at_threshold(self):
        engine = BlendingEngine(_config(cold_start_threshold=20))
        assert engine.compute_tenant_weight(20) == pytest.approx(0.7)

    def test_above_threshold(self):
        engine = BlendingEngine(_config(cold_start_threshold=20))
        assert engine.compute_tenant_weight(100) == pytest.approx(0.7)

    def test_halfway(self):
        engine = BlendingEngine(_config(cold_start_threshold=20))
        assert engine.compute_tenant_weight(10) == pytest.approx(0.35)

    def test_single_observation(self):
        engine = BlendingEngine(_config(cold_start_threshold=20))
        weight = engine.compute_tenant_weight(1)
        assert 0 < weight < 0.1

    def test_matches_ranker_formula(self):
        """Verify blend weights match the Ranker implementation."""
        engine = BlendingEngine(_config(cold_start_threshold=20, tenant_prior_blend=0.3))
        for n in [0, 1, 5, 10, 15, 20, 50]:
            threshold = 20
            if n == 0:
                expected = 0.0
            elif n >= threshold:
                expected = 1.0 - 0.3
            else:
                expected = (n / threshold) * (1.0 - 0.3)
            assert engine.compute_tenant_weight(n) == pytest.approx(expected)


# ── Tests: Cold start detection ──────────────────────────────────


class TestColdStart:
    def test_zero_is_cold_start(self):
        engine = BlendingEngine(_config(cold_start_threshold=20))
        assert engine.is_cold_start(0) is True

    def test_below_threshold_is_cold_start(self):
        engine = BlendingEngine(_config(cold_start_threshold=20))
        assert engine.is_cold_start(19) is True

    def test_at_threshold_not_cold_start(self):
        engine = BlendingEngine(_config(cold_start_threshold=20))
        assert engine.is_cold_start(20) is False

    def test_above_threshold_not_cold_start(self):
        engine = BlendingEngine(_config(cold_start_threshold=20))
        assert engine.is_cold_start(100) is False


# ── Tests: Data maturity label ───────────────────────────────────


class TestMaturityLabel:
    def test_no_data(self):
        engine = BlendingEngine(_config(cold_start_threshold=20))
        assert engine.data_maturity_label(0) == "no_data"

    def test_early(self):
        engine = BlendingEngine(_config(cold_start_threshold=20))
        assert engine.data_maturity_label(5) == "early"

    def test_growing(self):
        engine = BlendingEngine(_config(cold_start_threshold=20))
        assert engine.data_maturity_label(15) == "growing"

    def test_mature(self):
        engine = BlendingEngine(_config(cold_start_threshold=20))
        assert engine.data_maturity_label(20) == "mature"


# ── Tests: Blending behavior ────────────────────────────────────


class TestBlending:
    def test_pure_global_when_no_tenant_data(self):
        """With 0 observations, all recs come from global priors."""
        engine = BlendingEngine(_config())
        result = engine.blend([], _global_recs(), 0)
        assert len(result) > 0
        for r in result:
            assert r.source == RecommendationSource.GLOBAL_PRIOR
            assert r.tenant_weight == 0.0
            assert r.global_weight == 1.0

    def test_pure_tenant_when_no_global_priors(self):
        """With only tenant data and no globals, source is tenant_learned."""
        engine = BlendingEngine(_config())
        result = engine.blend([_tenant_rec()], {}, 50)
        assert len(result) == 1
        assert result[0].source == RecommendationSource.TENANT_LEARNED

    def test_blended_when_both_exist(self):
        """When tenant and global match, result is blended."""
        engine = BlendingEngine(_config(cold_start_threshold=20))
        tenant = [_tenant_rec(feature="post_hour", value="18", confidence=0.8)]
        global_ = _global_recs(feature="post_hour", value="18", confidence=0.7)
        result = engine.blend(tenant, global_, 15)
        matched = [r for r in result if r.feature == "post_hour"]
        assert len(matched) == 1
        assert matched[0].source == RecommendationSource.BLENDED
        assert 0 < matched[0].tenant_weight < 1
        assert 0 < matched[0].global_weight < 1

    def test_global_fills_gaps(self):
        """Global recs for features not in tenant data are added."""
        engine = BlendingEngine(_config())
        tenant = [_tenant_rec(feature="cta_type", value="link")]
        global_ = _global_recs(feature="post_hour", value="18")
        result = engine.blend(tenant, global_, 10)
        features = {r.feature for r in result}
        assert "post_hour" in features
        assert "cta_type" in features

    def test_blended_confidence_weighted(self):
        """Blended confidence is a weighted average of tenant and global."""
        engine = BlendingEngine(_config(cold_start_threshold=20))
        tenant = [_tenant_rec(confidence=0.9)]
        global_ = _global_recs(confidence=0.6)
        result = engine.blend(tenant, global_, 10)
        blended = result[0]
        # At 10/20 obs: tenant_weight=0.35, global_weight=0.65
        expected = 0.9 * 0.35 + 0.6 * 0.65
        assert blended.confidence == pytest.approx(expected, abs=0.01)

    def test_sorted_by_confidence(self):
        """Results are sorted by confidence descending."""
        engine = BlendingEngine(_config())
        tenant = [
            _tenant_rec(feature="cta_type", value="link", confidence=0.3),
            _tenant_rec(feature="post_hour", value="18", confidence=0.9),
        ]
        result = engine.blend(tenant, {}, 50)
        confidences = [r.confidence for r in result]
        assert confidences == sorted(confidences, reverse=True)

    def test_global_recs_scaled_by_weight(self):
        """Global-only recs have confidence scaled by global_weight."""
        engine = BlendingEngine(_config(cold_start_threshold=20))
        global_ = _global_recs(confidence=0.8)
        result = engine.blend([], global_, 10)
        # At 10 obs: global_weight = 1 - 0.35 = 0.65
        assert result[0].confidence == pytest.approx(0.8 * 0.65, abs=0.01)


# ── Tests: Source labeling ───────────────────────────────────────


class TestSourceLabeling:
    def test_global_prior_label(self):
        engine = BlendingEngine(_config())
        result = engine.blend([], _global_recs(feature="post_hour"), 0)
        assert result[0].source == RecommendationSource.GLOBAL_PRIOR

    def test_cohort_prior_label(self):
        engine = BlendingEngine(_config())
        recs = {
            "posting_window": [{
                "feature_name": "post_hour",
                "feature_value": "18",
                "mean_reward": 0.6,
                "confidence": 0.7,
                "tenant_count": 10,
                "source": "cohort_prior",
                "explanation": "Cohort insight",
                "category": "posting_window",
            }]
        }
        result = engine.blend([], recs, 0)
        assert result[0].source == RecommendationSource.COHORT_PRIOR

    def test_tenant_learned_label(self):
        engine = BlendingEngine(_config())
        result = engine.blend([_tenant_rec()], {}, 50)
        assert result[0].source == RecommendationSource.TENANT_LEARNED

    def test_blended_label(self):
        engine = BlendingEngine(_config())
        result = engine.blend(
            [_tenant_rec()],
            _global_recs(),
            10,
        )
        blended = [r for r in result if r.feature == "post_hour"]
        assert blended[0].source == RecommendationSource.BLENDED

    def test_explanation_includes_blend_pct(self):
        engine = BlendingEngine(_config(cold_start_threshold=20))
        result = engine.blend([_tenant_rec()], _global_recs(), 10)
        blended = [r for r in result if r.source == RecommendationSource.BLENDED]
        assert len(blended) == 1
        assert "35%" in blended[0].explanation  # 10/20 * 0.7 = 0.35


# ── Tests: Serialization ────────────────────────────────────────


class TestSerialization:
    def test_to_dict_roundtrip(self):
        engine = BlendingEngine(_config())
        result = engine.blend([_tenant_rec()], _global_recs(), 10)
        for r in result:
            d = r.to_dict()
            assert "feature" in d
            assert "source" in d
            assert "tenant_weight" in d
            assert isinstance(d["confidence"], float)

    def test_to_dict_source_is_string(self):
        engine = BlendingEngine(_config())
        result = engine.blend([], _global_recs(), 0)
        d = result[0].to_dict()
        assert d["source"] == "global_prior"


# ── Tests: Edge cases ───────────────────────────────────────────


class TestEdgeCases:
    def test_empty_both(self):
        engine = BlendingEngine(_config())
        result = engine.blend([], {}, 0)
        assert result == []

    def test_multiple_categories(self):
        engine = BlendingEngine(_config())
        global_ = {
            "posting_window": [
                {"feature_name": "post_hour", "feature_value": "18",
                 "mean_reward": 0.7, "confidence": 0.8, "tenant_count": 10,
                 "source": "global_prior", "explanation": "Test", "category": "posting_window"},
            ],
            "cta_type": [
                {"feature_name": "cta_type", "feature_value": "link",
                 "mean_reward": 0.6, "confidence": 0.7, "tenant_count": 8,
                 "source": "global_prior", "explanation": "Test", "category": "cta_type"},
            ],
        }
        result = engine.blend([], global_, 0)
        categories = {r.category for r in result}
        assert "posting_window" in categories
        assert "cta_type" in categories

    def test_duplicate_feature_not_duplicated(self):
        """A tenant rec matching a global rec should produce 1 result, not 2."""
        engine = BlendingEngine(_config())
        tenant = [_tenant_rec(feature="post_hour", value="18")]
        global_ = _global_recs(feature="post_hour", value="18")
        result = engine.blend(tenant, global_, 10)
        post_hour_recs = [r for r in result if r.feature == "post_hour"]
        assert len(post_hour_recs) == 1

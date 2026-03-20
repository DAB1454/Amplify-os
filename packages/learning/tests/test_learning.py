"""Tests for the learning subsystem foundation.

Covers config, schemas, feature extraction, rewards, tenant memory,
global priors (with privacy), ranking, and evaluation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from amplify.learning.config import (
    LearningConfig,
    MemoryConfig,
    PrivacyConfig,
    RankingConfig,
    RewardConfig,
)
from amplify.learning.schemas.observation import Observation, OutcomeMetrics
from amplify.learning.schemas.feature import (
    FeatureDefinition,
    FeatureSchema,
    FeatureType,
    FeatureVector,
)
from amplify.learning.schemas.insight import Insight, InsightType
from amplify.learning.schemas.score import RankingExplanation, ScoreComponent, ScoredAction
from amplify.learning.feature_extraction import (
    FeatureRegistry,
    extract_timing_features,
    extract_content_features,
    extract_platform_features,
)
from amplify.learning.rewards import RewardCalculator
from amplify.learning.tenant_memory import TenantMemoryStore
from amplify.learning.global_priors import GlobalPrior, PriorAggregator
from amplify.learning.privacy import PrivacyFilter
from amplify.learning.ranking import Ranker
from amplify.learning.evaluation import EvaluationReport, evaluate_predictions


TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()


# ── Config ─────────────────────────────────────────────────────────


class TestConfig:
    def test_default_config(self):
        cfg = LearningConfig()
        assert cfg.enabled is True
        assert cfg.privacy.k_anonymity_threshold == 5
        assert cfg.audit_enabled is True

    def test_local_preset(self):
        cfg = LearningConfig.local()
        assert cfg.privacy.k_anonymity_threshold == 1
        assert cfg.ranking.exploration_rate == 0.2

    def test_production_preset(self):
        cfg = LearningConfig.production()
        assert cfg.privacy.k_anonymity_threshold == 5
        assert cfg.rewards.min_observations == 10

    def test_disabled_preset(self):
        cfg = LearningConfig.disabled()
        assert cfg.enabled is False

    def test_for_environment(self):
        assert LearningConfig.for_environment("local").privacy.k_anonymity_threshold == 1
        assert LearningConfig.for_environment("production").privacy.k_anonymity_threshold == 5
        # Unknown falls back to production
        assert LearningConfig.for_environment("unknown").privacy.k_anonymity_threshold == 5

    def test_frozen(self):
        cfg = LearningConfig()
        with pytest.raises(AttributeError):
            cfg.enabled = False  # type: ignore[misc]


# ── Schemas ────────────────────────────────────────────────────────


class TestSchemas:
    def test_observation_immutable(self):
        obs = Observation(tenant_id=TENANT_A, action_type="publish_post")
        with pytest.raises(Exception):
            obs.action_type = "other"  # type: ignore[misc]

    def test_outcome_metrics_optional(self):
        m = OutcomeMetrics()
        assert m.impressions is None
        assert m.engagement_rate is None

    def test_feature_vector(self):
        fv = FeatureVector(values={"post_hour": 14, "platform": "instagram"})
        assert fv.get("post_hour") == 14
        assert "platform" in fv
        assert fv.get("missing", "default") == "default"

    def test_feature_schema_validation(self):
        schema = FeatureSchema()
        schema.register(FeatureDefinition(
            name="post_hour",
            feature_type=FeatureType.TEMPORAL,
        ))
        fv = FeatureVector(values={"post_hour": 14, "unknown_feat": "x"})
        warnings = schema.validate_vector(fv)
        assert "Unknown feature: unknown_feat" in warnings

    def test_insight_model(self):
        insight = Insight(
            tenant_id=TENANT_A,
            insight_type=InsightType.TIMING,
            title="Best posting hour",
            explanation="Posts at 18:00 get 2x engagement",
            confidence=0.85,
        )
        assert insight.confidence == 0.85
        assert insight.source == "tenant"

    def test_scored_action_explanation_summary(self):
        explanation = RankingExplanation(
            components=[
                ScoreComponent(
                    name="timing",
                    weight=1.0,
                    raw_value=0.8,
                    weighted_value=0.8,
                    explanation="Good posting time",
                ),
            ],
            tenant_weight=0.7,
            prior_weight=0.3,
            observation_count=50,
        )
        assert "tenant data" in explanation.summary
        assert "Good posting time" in explanation.summary


# ── Feature extraction ─────────────────────────────────────────────


class TestFeatureExtraction:
    def test_timing_features(self):
        dt = datetime(2026, 3, 20, 18, 30)  # Thursday 18:30
        features = extract_timing_features(published_at=dt)
        assert features["post_hour"] == 18
        assert features["post_day_of_week"] == 4  # Friday (0=Monday... wait, 2026-03-20 is a Friday)

    def test_timing_features_none(self):
        assert extract_timing_features() == {}

    def test_content_features(self):
        features = extract_content_features(
            content_text="Check out #newmusic at https://example.com",
            media_urls=["img1.jpg", "img2.jpg"],
        )
        assert features["content_type"] == "carousel"
        assert features["hashtag_count"] == 1
        assert features["has_link"] is True
        assert features["caption_length"] > 0

    def test_content_features_text_only(self):
        features = extract_content_features(content_text="Hello world")
        assert features["content_type"] == "text"
        assert features["has_link"] is False

    def test_platform_features(self):
        features = extract_platform_features(
            platform="instagram",
            campaign_phase="release_week",
        )
        assert features["platform"] == "instagram"
        assert features["campaign_phase"] == "release_week"

    def test_registry_has_default_features(self):
        schema = FeatureRegistry.schema()
        assert "post_hour" in schema.features
        assert "platform" in schema.features
        assert "content_type" in schema.features

    def test_registry_reset(self):
        FeatureRegistry.reset()
        schema = FeatureRegistry.schema()
        assert "post_hour" in schema.features


# ── Rewards ────────────────────────────────────────────────────────


class TestRewards:
    def test_basic_reward(self):
        calc = RewardCalculator()
        outcomes = OutcomeMetrics(
            engagement_rate=0.05,
            save_rate=0.02,
            click_through_rate=0.01,
            reach=5000,
        )
        breakdown = calc.compute(outcomes)
        assert 0.0 < breakdown.final_reward <= 1.0
        assert breakdown.decay_factor == 1.0  # no timestamp → no decay
        assert "engagement_rate" in breakdown.raw_components

    def test_temporal_decay(self):
        calc = RewardCalculator(RewardConfig(decay_half_life_days=30))
        outcomes = OutcomeMetrics(engagement_rate=0.05)
        now = datetime(2026, 3, 20)
        old = now - timedelta(days=30)

        recent = calc.compute(outcomes, observed_at=now - timedelta(days=1), now=now)
        aged = calc.compute(outcomes, observed_at=old, now=now)

        assert recent.decay_factor > aged.decay_factor
        assert recent.final_reward > aged.final_reward

    def test_missing_metrics(self):
        calc = RewardCalculator()
        breakdown = calc.compute(OutcomeMetrics())
        assert breakdown.final_reward == 0.0
        assert breakdown.raw_components == {}

    def test_breakdown_auditable(self):
        calc = RewardCalculator()
        outcomes = OutcomeMetrics(engagement_rate=0.08, reach=10000)
        breakdown = calc.compute(outcomes)
        assert len(breakdown.weighted_components) == 2
        # final_reward = raw_total * decay * confidence, so it's ≤ sum of weighted components
        raw_total = sum(breakdown.weighted_components.values())
        expected = raw_total * breakdown.decay_factor * breakdown.confidence
        assert abs(breakdown.final_reward - expected) < 1e-9


# ── Tenant memory ──────────────────────────────────────────────────


class TestTenantMemory:
    def test_record_and_retrieve(self):
        store = TenantMemoryStore()
        obs = Observation(tenant_id=TENANT_A, action_type="publish_post", reward=0.5)
        store.record(obs)

        retrieved = store.get_observations(TENANT_A)
        assert len(retrieved) == 1
        assert retrieved[0].reward == 0.5

    def test_tenant_isolation(self):
        store = TenantMemoryStore()
        store.record(Observation(tenant_id=TENANT_A, action_type="publish_post"))
        store.record(Observation(tenant_id=TENANT_B, action_type="publish_post"))

        assert store.observation_count(TENANT_A) == 1
        assert store.observation_count(TENANT_B) == 1

    def test_eviction(self):
        store = TenantMemoryStore(MemoryConfig(max_observations_per_tenant=3))
        for i in range(5):
            store.record(Observation(
                tenant_id=TENANT_A,
                action_type="publish_post",
                observed_at=datetime(2026, 1, 1) + timedelta(days=i),
            ))
        assert store.observation_count(TENANT_A) == 3
        # Oldest should be evicted
        obs = store.get_observations(TENANT_A)
        assert obs[0].observed_at > datetime(2026, 1, 2)

    def test_compaction(self):
        store = TenantMemoryStore(MemoryConfig(compaction_age_days=30))
        old = Observation(
            tenant_id=TENANT_A,
            action_type="publish_post",
            observed_at=datetime(2025, 1, 1),
        )
        recent = Observation(
            tenant_id=TENANT_A,
            action_type="publish_post",
            observed_at=datetime(2026, 3, 15),
        )
        store.record(old)
        store.record(recent)

        removed = store.compact(TENANT_A, now=datetime(2026, 3, 20))
        assert removed == 1
        assert store.observation_count(TENANT_A) == 1

    def test_insights(self):
        store = TenantMemoryStore()
        insight = Insight(
            tenant_id=TENANT_A,
            insight_type=InsightType.TIMING,
            title="Best hour",
            explanation="18:00 works best",
            confidence=0.9,
        )
        store.store_insight(insight)
        assert len(store.get_insights(TENANT_A)) == 1

    def test_stats(self):
        store = TenantMemoryStore()
        store.record(Observation(tenant_id=TENANT_A, action_type="publish_post"))
        stats = store.stats(TENANT_A)
        assert stats["observation_count"] == 1
        assert stats["insight_count"] == 0


# ── Privacy ────────────────────────────────────────────────────────


class TestPrivacy:
    def test_sanitize_strips_pii(self):
        pf = PrivacyFilter()
        obs = Observation(
            tenant_id=TENANT_A,
            artist_id=uuid.uuid4(),
            action_type="publish_post",
            action_id=uuid.uuid4(),
            features={
                "post_hour": 18,
                "artist_name": "Drew Baird",
                "platform": "instagram",
            },
            reward=0.7,
        )
        sanitized = pf.sanitize(obs)
        assert sanitized is not None
        assert sanitized.tenant_id == uuid.UUID(int=0)
        assert sanitized.artist_id is None
        assert sanitized.action_id is None
        assert "artist_name" not in sanitized.features
        assert sanitized.features["post_hour"] == 18
        assert sanitized.reward == 0.7

    def test_sanitize_disabled(self):
        pf = PrivacyFilter(PrivacyConfig(cross_tenant_enabled=False))
        obs = Observation(tenant_id=TENANT_A, action_type="publish_post")
        assert pf.sanitize(obs) is None

    def test_cardinality_check(self):
        pf = PrivacyFilter(PrivacyConfig(max_categorical_cardinality=3))
        obs = [
            Observation(
                tenant_id=TENANT_A,
                action_type="publish_post",
                features={"tag": f"unique_{i}"},
            )
            for i in range(10)
        ]
        assert pf.check_cardinality(obs, "tag") is False

        small = obs[:2]
        assert pf.check_cardinality(small, "tag") is True


# ── Global priors ──────────────────────────────────────────────────


class TestGlobalPriors:
    def _make_observations(self, n_tenants: int, feature_value: str, reward: float) -> list[Observation]:
        return [
            Observation(
                tenant_id=uuid.uuid4(),
                action_type="publish_post",
                features={"post_hour": feature_value},
                reward=reward,
            )
            for _ in range(n_tenants)
        ]

    def test_aggregation_meets_k_anonymity(self):
        cfg = LearningConfig(privacy=PrivacyConfig(k_anonymity_threshold=3))
        agg = PriorAggregator(cfg)

        # 5 tenants posting at hour 18 → meets k=3
        obs = self._make_observations(5, "18", 0.8)
        priors = agg.aggregate(obs, "post_hour")
        assert len(priors) == 1
        assert priors[0].feature_value == "18"
        assert priors[0].tenant_count == 5

    def test_aggregation_below_k_anonymity(self):
        cfg = LearningConfig(privacy=PrivacyConfig(k_anonymity_threshold=5))
        agg = PriorAggregator(cfg)

        # Only 3 tenants → below k=5 threshold
        obs = self._make_observations(3, "18", 0.8)
        priors = agg.aggregate(obs, "post_hour")
        assert len(priors) == 0

    def test_aggregation_disabled(self):
        cfg = LearningConfig(privacy=PrivacyConfig(cross_tenant_enabled=False))
        agg = PriorAggregator(cfg)
        obs = self._make_observations(10, "18", 0.8)
        assert agg.aggregate(obs, "post_hour") == []

    def test_prior_reliability(self):
        p = GlobalPrior(
            feature_name="post_hour",
            feature_value="18",
            mean_reward=0.7,
            observation_count=50,
            tenant_count=5,
        )
        assert p.is_reliable is True

        weak = GlobalPrior(
            feature_name="post_hour",
            feature_value="3",
            mean_reward=0.2,
            observation_count=3,
            tenant_count=1,
        )
        assert weak.is_reliable is False


# ── Ranking ────────────────────────────────────────────────────────


class TestRanking:
    def test_rank_with_tenant_data(self):
        cfg = LearningConfig.local()
        ranker = Ranker(cfg)

        obs = [
            Observation(
                tenant_id=TENANT_A,
                action_type="publish_post",
                features={"post_hour": "18"},
                reward=0.9,
            ),
            Observation(
                tenant_id=TENANT_A,
                action_type="publish_post",
                features={"post_hour": "3"},
                reward=0.2,
            ),
        ]

        candidates = [
            {"action_type": "publish_post", "post_hour": "18"},
            {"action_type": "publish_post", "post_hour": "3"},
        ]

        # Seed random for deterministic test
        import random
        random.seed(42)

        scored = ranker.rank(candidates, obs)
        assert len(scored) == 2
        assert scored[0].rank == 1
        # Hour 18 should score higher
        assert scored[0].action_params["post_hour"] == "18"

    def test_cold_start_uses_priors(self):
        cfg = LearningConfig(ranking=RankingConfig(cold_start_threshold=10))
        ranker = Ranker(cfg)

        priors = [
            GlobalPrior(
                feature_name="post_hour",
                feature_value="18",
                mean_reward=0.8,
                observation_count=100,
                tenant_count=10,
            ),
        ]

        candidates = [
            {"action_type": "publish_post", "post_hour": "18"},
            {"action_type": "publish_post", "post_hour": "3"},
        ]

        import random
        random.seed(42)

        scored = ranker.rank(candidates, [], global_priors=priors)
        assert scored[0].action_params["post_hour"] == "18"
        assert scored[0].explanation.prior_weight > 0

    def test_explanations_present(self):
        ranker = Ranker(LearningConfig.local())
        obs = [
            Observation(
                tenant_id=TENANT_A,
                action_type="publish_post",
                features={"platform": "instagram"},
                reward=0.7,
            ),
        ]
        import random
        random.seed(42)

        scored = ranker.rank(
            [{"action_type": "publish_post", "platform": "instagram"}],
            obs,
        )
        assert len(scored) == 1
        assert len(scored[0].explanation.components) > 0
        assert scored[0].explanation.summary  # non-empty


# ── Evaluation ─────────────────────────────────────────────────────


class TestEvaluation:
    def test_perfect_predictions(self):
        action_ids = [uuid.uuid4() for _ in range(5)]
        predictions = [
            ScoredAction(action_id=aid, score=float(i) / 5)
            for i, aid in enumerate(action_ids)
        ]
        actuals = [
            Observation(
                tenant_id=TENANT_A,
                action_type="publish_post",
                action_id=aid,
                reward=float(i) / 5,
            )
            for i, aid in enumerate(action_ids)
        ]

        report = evaluate_predictions(predictions, actuals)
        assert report.sample_size == 5
        assert report.mae is not None and report.mae < 0.01
        assert report.rank_correlation is not None and report.rank_correlation > 0.9

    def test_no_matches(self):
        report = evaluate_predictions([], [])
        assert report.sample_size == 0
        assert report.mae is None

    def test_report_is_useful(self):
        r = EvaluationReport(rank_correlation=0.5, sample_size=10)
        assert r.is_useful is True

        r2 = EvaluationReport(rank_correlation=0.1, sample_size=10)
        assert r2.is_useful is False

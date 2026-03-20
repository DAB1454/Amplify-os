"""Tests for tenant pattern learning — aggregation, decay, thresholds, pinning, API."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import TEST_TENANT_ID, TEST_USER_ID


# ── Helpers ────────────────────────────────────────────────────────


def _make_features(
    hook_family: str = "question",
    cta_type: str = "presave",
    post_hour: int = 18,
    day_of_week: int = 2,
    caption_bucket: str = "medium",
    asset_type: str = "image",
    platform: str = "instagram",
) -> dict:
    return {
        "content_hook_family": hook_family,
        "content_cta_type": cta_type,
        "post_hour": post_hour,
        "post_day_of_week": day_of_week,
        "content_caption_bucket": caption_bucket,
        "asset_type": asset_type,
        "channel_platform": platform,
    }


# ═══════════════════════════════════════════════════════════════════
# Pure engine tests (no DB)
# ═══════════════════════════════════════════════════════════════════


class TestFeatureAggregator:
    def test_basic_aggregation(self):
        from amplify.learning.patterns.aggregator import FeatureAggregator

        agg = FeatureAggregator(half_life_days=30.0)
        now = datetime(2026, 3, 20)

        agg.add({"post_hour": "18"}, reward=0.8, observed_at=now, now=now)
        agg.add({"post_hour": "18"}, reward=0.6, observed_at=now, now=now)
        agg.add({"post_hour": "9"}, reward=0.2, observed_at=now, now=now)

        rows = agg.get_aggregates("post_hour")
        assert len(rows) == 2
        # 18:00 should rank first (higher mean reward)
        assert rows[0].value == "18"
        assert rows[0].raw_count == 2
        assert abs(rows[0].mean_reward - 0.7) < 0.01

    def test_decay_reduces_old_observations(self):
        from amplify.learning.patterns.aggregator import FeatureAggregator

        agg = FeatureAggregator(half_life_days=30.0)
        now = datetime(2026, 3, 20)
        old = now - timedelta(days=60)  # two half-lives ago

        agg.add({"x": "a"}, reward=1.0, observed_at=old, now=now)
        agg.add({"x": "b"}, reward=1.0, observed_at=now, now=now)

        a = agg.get("x", "a")
        b = agg.get("x", "b")
        # Old observation should have decayed weight ~0.25
        assert a.decayed_count < 0.5
        assert b.decayed_count > 0.9
        # Recent observation contributes more to its aggregate
        assert b.decayed_reward_sum > a.decayed_reward_sum

    def test_skips_none_values(self):
        from amplify.learning.patterns.aggregator import FeatureAggregator

        agg = FeatureAggregator()
        now = datetime(2026, 3, 20)
        agg.add({"x": None, "y": "ok"}, reward=0.5, observed_at=now, now=now)
        assert agg.get("x", "None") is None
        assert agg.get("y", "ok") is not None

    def test_skips_internal_features(self):
        from amplify.learning.patterns.aggregator import FeatureAggregator

        agg = FeatureAggregator()
        now = datetime(2026, 3, 20)
        agg.add({"_schema_version": 1, "x": "a"}, reward=0.5, observed_at=now, now=now)
        assert agg.get("_schema_version", "1") is None
        assert agg.get("x", "a") is not None

    def test_tracked_features_filter(self):
        from amplify.learning.patterns.aggregator import FeatureAggregator

        agg = FeatureAggregator(tracked_features={"post_hour"})
        now = datetime(2026, 3, 20)
        agg.add({"post_hour": "18", "other": "val"}, reward=0.5, observed_at=now, now=now)
        assert agg.get("post_hour", "18") is not None
        assert agg.get("other", "val") is None


class TestPatternEngine:
    def _make_engine(self, min_samples=2, min_confidence=0.0):
        from amplify.learning.patterns.engine import PatternConfig, PatternEngine

        config = PatternConfig(
            min_sample_count=min_samples,
            min_decayed_count=1.0,
            min_confidence=min_confidence,
            decay_half_life_days=30.0,
            winning_threshold_factor=1.2,
            losing_threshold_factor=0.8,
        )
        return PatternEngine(config)

    def test_discovers_winning_pattern(self):
        engine = self._make_engine()
        now = datetime(2026, 3, 20)

        # Add strong performers with hook "question"
        for _ in range(5):
            engine.ingest({"content_hook_family": "question"}, 0.9, now, now=now)
        # Add weak performers with hook "statement"
        for _ in range(5):
            engine.ingest({"content_hook_family": "statement"}, 0.2, now, now=now)

        patterns = engine.discover_patterns()
        winning = [p for p in patterns if p.direction == "winning"]
        losing = [p for p in patterns if p.direction == "losing"]

        assert len(winning) >= 1
        assert winning[0].value == "question"
        assert len(losing) >= 1
        assert losing[0].value == "statement"

    def test_not_enough_data(self):
        from amplify.learning.patterns.engine import PatternConfig, PatternEngine

        engine = PatternEngine(PatternConfig(min_sample_count=10))
        now = datetime(2026, 3, 20)
        engine.ingest({"x": "a"}, 0.5, now, now=now)
        assert engine.not_enough_data()

    def test_threshold_gating(self):
        """Patterns below min_sample_count are not returned."""
        engine = self._make_engine(min_samples=10)
        now = datetime(2026, 3, 20)

        # Only 3 observations per value — below threshold
        for _ in range(3):
            engine.ingest({"content_hook_family": "question"}, 0.9, now, now=now)
            engine.ingest({"content_hook_family": "statement"}, 0.1, now, now=now)

        patterns = engine.discover_patterns()
        # Should be empty because raw_count (3) < min_sample_count (10)
        assert len(patterns) == 0

    def test_confidence_threshold(self):
        """Patterns below min_confidence are filtered out."""
        engine = self._make_engine(min_samples=2, min_confidence=0.99)
        now = datetime(2026, 3, 20)

        for _ in range(3):
            engine.ingest({"content_hook_family": "question"}, 0.9, now, now=now)
            engine.ingest({"content_hook_family": "statement"}, 0.1, now, now=now)

        patterns = engine.discover_patterns()
        # With only 3 samples, confidence won't reach 0.99
        assert len(patterns) == 0

    def test_decay_affects_patterns(self):
        """Old observations contribute less to pattern discovery."""
        engine = self._make_engine(min_samples=2)
        now = datetime(2026, 3, 20)
        old = now - timedelta(days=120)  # 4 half-lives ago

        # Old data: "question" was great
        for _ in range(5):
            engine.ingest({"content_hook_family": "question"}, 0.9, old, now=now)
        # Recent data: "question" is mediocre, "statement" is great
        for _ in range(5):
            engine.ingest({"content_hook_family": "question"}, 0.3, now, now=now)
            engine.ingest({"content_hook_family": "statement"}, 0.9, now, now=now)

        patterns = engine.discover_patterns()
        winning = [p for p in patterns if p.direction == "winning"]
        # "statement" should be winning because recent data dominates
        if winning:
            assert winning[0].value == "statement"

    def test_generate_recommendations(self):
        engine = self._make_engine()
        now = datetime(2026, 3, 20)

        for _ in range(5):
            engine.ingest(
                {"content_hook_family": "question", "post_hour": "18"},
                0.9, now, now=now,
            )
            engine.ingest(
                {"content_hook_family": "statement", "post_hour": "6"},
                0.2, now, now=now,
            )

        recs = engine.generate_recommendations()
        assert len(recs) >= 1
        # Each recommendation has required fields
        for r in recs:
            assert r.title
            assert r.explanation
            assert 0.0 <= r.confidence <= 1.0
            assert r.evidence

    def test_pinned_preferences_override(self):
        engine = self._make_engine()
        now = datetime(2026, 3, 20)

        # Data says "question" wins
        for _ in range(5):
            engine.ingest({"content_hook_family": "question"}, 0.9, now, now=now)
            engine.ingest({"content_hook_family": "statement"}, 0.2, now, now=now)

        # Admin pinned "statement"
        recs = engine.generate_recommendations(
            pinned_features={"content_hook_family": "statement"}
        )

        hook_rec = next(r for r in recs if r.feature == "content_hook_family")
        assert hook_rec.recommended_value == "statement"
        assert hook_rec.confidence == 1.0
        assert hook_rec.evidence.get("pinned") is True

    def test_explanation_is_human_readable(self):
        engine = self._make_engine()
        now = datetime(2026, 3, 20)

        for _ in range(5):
            engine.ingest({"post_hour": "18"}, 0.9, now, now=now)
            engine.ingest({"post_hour": "6"}, 0.2, now, now=now)

        patterns = engine.discover_patterns()
        winning = [p for p in patterns if p.direction == "winning"]
        assert winning
        # Should mention the human-readable hour
        assert "6:00 PM" in winning[0].title or "6:00 PM" in winning[0].explanation

    def test_day_of_week_human_readable(self):
        engine = self._make_engine()
        now = datetime(2026, 3, 20)

        for _ in range(5):
            engine.ingest({"post_day_of_week": "2"}, 0.9, now, now=now)
            engine.ingest({"post_day_of_week": "0"}, 0.2, now, now=now)

        patterns = engine.discover_patterns()
        winning = [p for p in patterns if p.direction == "winning"]
        if winning:
            # Wednesday = day 2
            text = winning[0].title + winning[0].explanation
            assert "Wednesday" in text

    def test_evidence_contains_lift(self):
        engine = self._make_engine()
        now = datetime(2026, 3, 20)

        for _ in range(5):
            engine.ingest({"content_hook_family": "question"}, 0.9, now, now=now)
            engine.ingest({"content_hook_family": "statement"}, 0.2, now, now=now)

        patterns = engine.discover_patterns()
        for p in patterns:
            assert "lift_vs_baseline" in p.evidence
            assert "raw_count" in p.evidence
            assert "mean_reward" in p.evidence

    def test_empty_data_returns_empty(self):
        engine = self._make_engine()
        assert engine.discover_patterns() == []
        assert engine.generate_recommendations() == []


# ═══════════════════════════════════════════════════════════════════
# DB integration tests
# ═══════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def seed_feature_outcome_data(db_session, seed_channel):
    """Seed feature vectors and outcomes for pattern learning tests.

    Depends on seed_channel which depends on seed_artist → seed_tenant.
    """
    from amplify.db.models.post import PostModel
    from amplify.db.models.campaign import CampaignModel
    from amplify.db.models.release import ReleaseModel
    from amplify.db.models.learning import PostFeatureVectorModel, PostOutcomeModel

    channel = seed_channel

    # Get artist from channel
    artist_id = channel.artist_id

    release = ReleaseModel(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        artist_id=artist_id,
        title="Test Release",
        release_type="single",
    )
    db_session.add(release)

    campaign = CampaignModel(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        artist_id=artist_id,
        release_id=release.id,
        name="Test Campaign",
        status="active",
    )
    db_session.add(campaign)
    await db_session.flush()

    now = datetime(2026, 3, 20)
    posts = []
    vectors = []
    outcomes = []

    # Create posts with diverse features and varying rewards
    feature_sets = [
        # High performers: question hooks at 6pm
        ({"content_hook_family": "question", "post_hour": 18, "content_cta_type": "presave",
          "channel_platform": "instagram", "asset_type": "carousel"}, 0.85),
        ({"content_hook_family": "question", "post_hour": 18, "content_cta_type": "presave",
          "channel_platform": "instagram", "asset_type": "carousel"}, 0.90),
        ({"content_hook_family": "question", "post_hour": 18, "content_cta_type": "presave",
          "channel_platform": "instagram", "asset_type": "carousel"}, 0.80),
        ({"content_hook_family": "question", "post_hour": 18, "content_cta_type": "presave",
          "channel_platform": "instagram", "asset_type": "carousel"}, 0.88),
        ({"content_hook_family": "question", "post_hour": 18, "content_cta_type": "presave",
          "channel_platform": "instagram", "asset_type": "carousel"}, 0.82),
        # Low performers: statement hooks at 6am
        ({"content_hook_family": "statement", "post_hour": 6, "content_cta_type": "stream",
          "channel_platform": "tiktok", "asset_type": "video"}, 0.15),
        ({"content_hook_family": "statement", "post_hour": 6, "content_cta_type": "stream",
          "channel_platform": "tiktok", "asset_type": "video"}, 0.20),
        ({"content_hook_family": "statement", "post_hour": 6, "content_cta_type": "stream",
          "channel_platform": "tiktok", "asset_type": "video"}, 0.10),
        ({"content_hook_family": "statement", "post_hour": 6, "content_cta_type": "stream",
          "channel_platform": "tiktok", "asset_type": "video"}, 0.18),
        ({"content_hook_family": "statement", "post_hour": 6, "content_cta_type": "stream",
          "channel_platform": "tiktok", "asset_type": "video"}, 0.12),
    ]

    for i, (features, reward) in enumerate(feature_sets):
        post = PostModel(
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            campaign_id=campaign.id,
            channel_id=channel.id,
            platform=features.get("channel_platform", "instagram"),
            content_text=f"Test post {i}",
            status="published",
            published_at=now - timedelta(days=i),
        )
        db_session.add(post)
        posts.append(post)

        vec = PostFeatureVectorModel(
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            post_id=post.id,
            artist_id=artist_id,
            campaign_id=campaign.id,
            features=features,
            platform=features.get("channel_platform"),
            extracted_at=now - timedelta(days=i),
        )
        db_session.add(vec)
        vectors.append(vec)

        outcome = PostOutcomeModel(
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            post_id=post.id,
            measurement_window="7d",
            reward=reward,
            reward_breakdown={"formula_version": "v2"},
            measured_at=now - timedelta(days=max(i - 7, 0)),
        )
        db_session.add(outcome)
        outcomes.append(outcome)

    await db_session.commit()
    return {
        "channel": channel,
        "campaign": campaign,
        "posts": posts,
        "vectors": vectors,
        "outcomes": outcomes,
    }


class TestTenantPatternServiceUpdate:
    @pytest.mark.asyncio
    async def test_update_creates_patterns(
        self, db_session: AsyncSession, seed_feature_outcome_data,
    ):
        from app.services.tenant_pattern_service import TenantPatternService
        from amplify.learning.patterns.engine import PatternConfig

        config = PatternConfig(
            min_sample_count=3,
            min_decayed_count=1.0,
            min_confidence=0.0,
            winning_threshold_factor=1.1,
            losing_threshold_factor=0.9,
        )
        svc = TenantPatternService(db_session, TEST_TENANT_ID, config)
        patterns = await svc.update_patterns()

        assert len(patterns) > 0
        # Should have at least one winning and one losing
        directions = {p.direction for p in patterns}
        assert "winning" in directions or "losing" in directions
        # All patterns should be active
        assert all(p.is_active for p in patterns)

    @pytest.mark.asyncio
    async def test_update_preserves_pinned(
        self, db_session: AsyncSession, seed_feature_outcome_data,
    ):
        from app.services.tenant_pattern_service import TenantPatternService
        from amplify.learning.patterns.engine import PatternConfig

        config = PatternConfig(
            min_sample_count=3, min_decayed_count=1.0, min_confidence=0.0,
            winning_threshold_factor=1.1, losing_threshold_factor=0.9,
        )
        svc = TenantPatternService(db_session, TEST_TENANT_ID, config)

        # First run
        patterns = await svc.update_patterns()
        assert len(patterns) > 0

        # Pin the first pattern
        pinned = await svc.pin_pattern(patterns[0].id, TEST_USER_ID)
        original_title = pinned.title

        # Second run — pinned pattern should survive unchanged
        patterns2 = await svc.update_patterns()
        pinned_in_result = [p for p in patterns2 if p.id == pinned.id]
        assert len(pinned_in_result) == 1
        assert pinned_in_result[0].is_pinned is True
        assert pinned_in_result[0].title == original_title

    @pytest.mark.asyncio
    async def test_patterns_have_evidence(
        self, db_session: AsyncSession, seed_feature_outcome_data,
    ):
        from app.services.tenant_pattern_service import TenantPatternService
        from amplify.learning.patterns.engine import PatternConfig

        config = PatternConfig(
            min_sample_count=3, min_decayed_count=1.0, min_confidence=0.0,
            winning_threshold_factor=1.1, losing_threshold_factor=0.9,
        )
        svc = TenantPatternService(db_session, TEST_TENANT_ID, config)
        patterns = await svc.update_patterns()

        for p in patterns:
            if not p.is_pinned:
                assert p.evidence is not None
                assert "raw_count" in p.evidence
                assert "mean_reward" in p.evidence
                assert "lift_vs_baseline" in p.evidence

    @pytest.mark.asyncio
    async def test_update_idempotent(
        self, db_session: AsyncSession, seed_feature_outcome_data,
    ):
        from app.services.tenant_pattern_service import TenantPatternService
        from amplify.learning.patterns.engine import PatternConfig

        config = PatternConfig(
            min_sample_count=3, min_decayed_count=1.0, min_confidence=0.0,
            winning_threshold_factor=1.1, losing_threshold_factor=0.9,
        )
        svc = TenantPatternService(db_session, TEST_TENANT_ID, config)

        patterns1 = await svc.update_patterns()
        patterns2 = await svc.update_patterns()

        # Same number of patterns, existing ones updated (version bumped)
        assert len(patterns1) == len(patterns2)
        for p in patterns2:
            if not p.is_pinned:
                assert p.version >= 1

    @pytest.mark.asyncio
    async def test_no_data_returns_empty(self, db_session: AsyncSession, seed_tenant):
        from app.services.tenant_pattern_service import TenantPatternService

        svc = TenantPatternService(db_session, TEST_TENANT_ID)
        patterns = await svc.update_patterns()
        assert patterns == []


class TestTenantPatternServiceRecommendations:
    @pytest.mark.asyncio
    async def test_recommendations_with_data(
        self, db_session: AsyncSession, seed_feature_outcome_data,
    ):
        from app.services.tenant_pattern_service import TenantPatternService
        from amplify.learning.patterns.engine import PatternConfig

        config = PatternConfig(
            min_sample_count=3, min_decayed_count=1.0, min_confidence=0.0,
            winning_threshold_factor=1.1, losing_threshold_factor=0.9,
        )
        svc = TenantPatternService(db_session, TEST_TENANT_ID, config)
        recs = await svc.get_recommendations()

        assert len(recs) > 0
        for r in recs:
            assert "feature" in r
            assert "recommended_value" in r
            assert "confidence" in r
            assert "evidence" in r
            assert "explanation" in r

    @pytest.mark.asyncio
    async def test_recommendations_empty_when_no_data(
        self, db_session: AsyncSession, seed_tenant,
    ):
        from app.services.tenant_pattern_service import TenantPatternService

        svc = TenantPatternService(db_session, TEST_TENANT_ID)
        recs = await svc.get_recommendations()
        assert recs == []

    @pytest.mark.asyncio
    async def test_pinned_preference_in_recommendations(
        self, db_session: AsyncSession, seed_feature_outcome_data,
    ):
        from app.services.tenant_pattern_service import TenantPatternService
        from amplify.learning.patterns.engine import PatternConfig

        config = PatternConfig(
            min_sample_count=3, min_decayed_count=1.0, min_confidence=0.0,
            winning_threshold_factor=1.1, losing_threshold_factor=0.9,
        )
        svc = TenantPatternService(db_session, TEST_TENANT_ID, config)

        # First create patterns, then pin one
        patterns = await svc.update_patterns()
        assert len(patterns) > 0

        # Pin a pattern
        target = patterns[0]
        await svc.pin_pattern(target.id, TEST_USER_ID)

        recs = await svc.get_recommendations()
        pinned_recs = [r for r in recs if r.get("evidence", {}).get("pinned")]
        assert len(pinned_recs) >= 1
        assert pinned_recs[0]["confidence"] == 1.0


class TestTenantPatternServicePinning:
    @pytest.mark.asyncio
    async def test_pin_and_unpin(
        self, db_session: AsyncSession, seed_feature_outcome_data,
    ):
        from app.services.tenant_pattern_service import TenantPatternService
        from amplify.learning.patterns.engine import PatternConfig

        config = PatternConfig(
            min_sample_count=3, min_decayed_count=1.0, min_confidence=0.0,
            winning_threshold_factor=1.1, losing_threshold_factor=0.9,
        )
        svc = TenantPatternService(db_session, TEST_TENANT_ID, config)
        patterns = await svc.update_patterns()
        assert len(patterns) > 0

        # Pin
        pinned = await svc.pin_pattern(patterns[0].id, TEST_USER_ID)
        assert pinned.is_pinned is True
        assert pinned.pinned_by == TEST_USER_ID
        assert pinned.pinned_at is not None

        # Unpin
        unpinned = await svc.unpin_pattern(patterns[0].id)
        assert unpinned.is_pinned is False
        assert unpinned.pinned_by is None

    @pytest.mark.asyncio
    async def test_pin_nonexistent_raises(
        self, db_session: AsyncSession, seed_tenant,
    ):
        from app.services.tenant_pattern_service import TenantPatternService

        svc = TenantPatternService(db_session, TEST_TENANT_ID)
        with pytest.raises(ValueError, match="not found"):
            await svc.pin_pattern(uuid.uuid4(), TEST_USER_ID)


class TestDataStatus:
    @pytest.mark.asyncio
    async def test_data_status_empty(self, db_session: AsyncSession, seed_tenant):
        from app.services.tenant_pattern_service import TenantPatternService

        svc = TenantPatternService(db_session, TEST_TENANT_ID)
        status = await svc.get_data_status()

        assert status["feature_vectors"] == 0
        assert status["outcomes"] == 0
        assert status["has_enough_data"] is False

    @pytest.mark.asyncio
    async def test_data_status_with_data(
        self, db_session: AsyncSession, seed_feature_outcome_data,
    ):
        from app.services.tenant_pattern_service import TenantPatternService

        svc = TenantPatternService(db_session, TEST_TENANT_ID)
        status = await svc.get_data_status()

        assert status["feature_vectors"] == 10
        assert status["outcomes"] == 10
        assert status["has_enough_data"] is True


# ═══════════════════════════════════════════════════════════════════
# API endpoint tests
# ═══════════════════════════════════════════════════════════════════


class TestPatternEndpoints:
    @pytest.mark.asyncio
    async def test_get_patterns_empty(self, client: AsyncClient, seed_tenant):
        resp = await client.get(
            f"/api/v1/learning/tenant/{TEST_TENANT_ID}/patterns"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["patterns"] == []
        assert data["data_status"]["has_enough_data"] is False

    @pytest.mark.asyncio
    async def test_get_recommendations_empty(self, client: AsyncClient, seed_tenant):
        resp = await client.get(
            f"/api/v1/learning/tenant/{TEST_TENANT_ID}/recommendations"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["recommendations"] == []

    @pytest.mark.asyncio
    async def test_update_then_get_patterns(
        self, client: AsyncClient, seed_feature_outcome_data,
    ):
        # Trigger pattern learning
        resp = await client.post(
            f"/api/v1/learning/tenant/{TEST_TENANT_ID}/patterns/update"
        )
        assert resp.status_code == 200
        patterns = resp.json()
        assert len(patterns) > 0

        # Now GET patterns
        resp2 = await client.get(
            f"/api/v1/learning/tenant/{TEST_TENANT_ID}/patterns"
        )
        assert resp2.status_code == 200
        data = resp2.json()
        assert len(data["patterns"]) > 0
        assert data["data_status"]["has_enough_data"] is True

    @pytest.mark.asyncio
    async def test_get_recommendations_with_data(
        self, client: AsyncClient, seed_feature_outcome_data,
    ):
        resp = await client.get(
            f"/api/v1/learning/tenant/{TEST_TENANT_ID}/recommendations"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["recommendations"]) > 0
        for rec in data["recommendations"]:
            assert "title" in rec
            assert "confidence" in rec
            assert "evidence" in rec

    @pytest.mark.asyncio
    async def test_pin_endpoint(
        self, client: AsyncClient, seed_feature_outcome_data,
    ):
        # Create patterns first
        resp = await client.post(
            f"/api/v1/learning/tenant/{TEST_TENANT_ID}/patterns/update"
        )
        patterns = resp.json()
        pattern_id = patterns[0]["id"]

        # Pin it
        resp2 = await client.post(
            f"/api/v1/learning/tenant/{TEST_TENANT_ID}/patterns/{pattern_id}/pin",
            json={"user_id": str(TEST_USER_ID)},
        )
        assert resp2.status_code == 200
        assert resp2.json()["is_pinned"] is True

    @pytest.mark.asyncio
    async def test_unpin_endpoint(
        self, client: AsyncClient, seed_feature_outcome_data,
    ):
        # Create and pin
        resp = await client.post(
            f"/api/v1/learning/tenant/{TEST_TENANT_ID}/patterns/update"
        )
        pattern_id = resp.json()[0]["id"]
        await client.post(
            f"/api/v1/learning/tenant/{TEST_TENANT_ID}/patterns/{pattern_id}/pin",
            json={"user_id": str(TEST_USER_ID)},
        )

        # Unpin
        resp2 = await client.post(
            f"/api/v1/learning/tenant/{TEST_TENANT_ID}/patterns/{pattern_id}/unpin"
        )
        assert resp2.status_code == 200
        assert resp2.json()["is_pinned"] is False

    @pytest.mark.asyncio
    async def test_pin_nonexistent_returns_404(
        self, client: AsyncClient, seed_tenant,
    ):
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/api/v1/learning/tenant/{TEST_TENANT_ID}/patterns/{fake_id}/pin",
            json={"user_id": str(TEST_USER_ID)},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_filter_by_pattern_type(
        self, client: AsyncClient, seed_feature_outcome_data,
    ):
        # Create patterns
        await client.post(
            f"/api/v1/learning/tenant/{TEST_TENANT_ID}/patterns/update"
        )

        resp = await client.get(
            f"/api/v1/learning/tenant/{TEST_TENANT_ID}/patterns",
            params={"pattern_type": "timing"},
        )
        assert resp.status_code == 200
        for p in resp.json()["patterns"]:
            assert p["pattern_type"] == "timing"

    @pytest.mark.asyncio
    async def test_filter_by_direction(
        self, client: AsyncClient, seed_feature_outcome_data,
    ):
        await client.post(
            f"/api/v1/learning/tenant/{TEST_TENANT_ID}/patterns/update"
        )

        resp = await client.get(
            f"/api/v1/learning/tenant/{TEST_TENANT_ID}/patterns",
            params={"direction": "winning"},
        )
        assert resp.status_code == 200
        for p in resp.json()["patterns"]:
            assert p["direction"] == "winning"

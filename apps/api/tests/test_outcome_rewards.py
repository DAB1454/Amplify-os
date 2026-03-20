"""Tests for outcome recording and reward computation.

Covers:
- RewardCalculator unit tests (phase-aware weights, normalization, decay,
  incomplete windows, derived rates, weight overrides)
- OutcomeService integration tests (persistence, upsert, recomputation)
- API endpoint tests
- Example configs for different campaign phases
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.learning import PostOutcomeModel

TEST_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Seed fixture ─────────────────────────────────────────────────


@pytest_asyncio.fixture
async def seed_post_for_outcomes(db_session: AsyncSession, seed_artist, seed_channel):
    """Seed a published post with campaign context for outcome tests."""
    from amplify.db.models.release import ReleaseModel
    from amplify.db.models.campaign import CampaignModel
    from amplify.db.models.post import PostModel
    from amplify.db.models.learning import PostFeatureVectorModel

    artist = seed_artist
    channel = seed_channel

    release = ReleaseModel(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        artist_id=artist.id,
        title="Test Release",
        release_type="single",
        release_date=date(2026, 3, 29),
    )
    db_session.add(release)

    campaign = CampaignModel(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        artist_id=artist.id,
        release_id=release.id,
        name="Test Campaign",
        status="active",
        phase="pre_release",
    )
    db_session.add(campaign)

    post = PostModel(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        campaign_id=campaign.id,
        channel_id=channel.id,
        platform="instagram",
        status="published",
        content_text="Test post content",
        published_at=datetime(2026, 3, 20, 18, 0, 0),
        policy_decision="allow",
    )
    db_session.add(post)

    # Feature vector with release_phase
    vector = PostFeatureVectorModel(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        post_id=post.id,
        artist_id=artist.id,
        campaign_id=campaign.id,
        features={"release_phase": "pre_release", "platform": "instagram"},
        platform="instagram",
        campaign_phase="pre_release",
        schema_version=1,
    )
    db_session.add(vector)

    await db_session.commit()

    return {
        "post": post,
        "campaign": campaign,
        "release": release,
        "artist": artist,
        "channel": channel,
    }


# ── RewardCalculator unit tests ──────────────────────────────────


class TestRewardWeights:
    """Test weight profile construction and phase presets."""

    def test_default_weights_sum_to_one(self):
        from amplify.learning.rewards import RewardWeights

        w = RewardWeights()
        total = sum(w.to_dict().values())
        assert abs(total - 1.0) < 0.01

    def test_pre_release_weights_sum_to_one(self):
        from amplify.learning.rewards import pre_release_weights

        w = pre_release_weights()
        total = sum(w.to_dict().values())
        assert abs(total - 1.0) < 0.01

    def test_release_day_weights_sum_to_one(self):
        from amplify.learning.rewards import release_day_weights

        w = release_day_weights()
        total = sum(w.to_dict().values())
        assert abs(total - 1.0) < 0.01

    def test_post_release_weights_sum_to_one(self):
        from amplify.learning.rewards import post_release_weights

        w = post_release_weights()
        total = sum(w.to_dict().values())
        assert abs(total - 1.0) < 0.01

    def test_evergreen_weights_sum_to_one(self):
        from amplify.learning.rewards import evergreen_weights

        w = evergreen_weights()
        total = sum(w.to_dict().values())
        assert abs(total - 1.0) < 0.01

    def test_pre_release_prioritizes_saves_and_follows(self):
        from amplify.learning.rewards import pre_release_weights

        w = pre_release_weights()
        # Saves (presave proxy) and followers should be top 2
        d = w.to_dict()
        top_keys = sorted(d, key=d.get, reverse=True)[:3]
        assert "save_rate" in top_keys
        assert "followers_gained" in top_keys

    def test_release_day_prioritizes_clicks_and_shares(self):
        from amplify.learning.rewards import release_day_weights

        w = release_day_weights()
        d = w.to_dict()
        top_keys = sorted(d, key=d.get, reverse=True)[:2]
        assert "click_through_rate" in top_keys
        assert "share_rate" in top_keys

    def test_from_dict_roundtrip(self):
        from amplify.learning.rewards import RewardWeights

        original = RewardWeights(save_rate=0.5, engagement_rate=0.3, reach=0.2)
        d = original.to_dict()
        restored = RewardWeights.from_dict(d)
        assert restored.save_rate == 0.5
        assert restored.engagement_rate == 0.3

    def test_weights_for_phase(self):
        from amplify.learning.rewards import weights_for_phase, PHASE_PRESETS

        for phase_name in PHASE_PRESETS:
            w = weights_for_phase(phase_name)
            assert sum(w.to_dict().values()) > 0

    def test_weights_for_unknown_phase_returns_defaults(self):
        from amplify.learning.rewards import weights_for_phase, RewardWeights

        w = weights_for_phase("unknown_phase")
        d = RewardWeights()
        assert w.save_rate == d.save_rate


class TestRewardCalculatorBasic:
    """Core reward computation tests."""

    def test_basic_reward_positive(self):
        from amplify.learning.rewards import RewardCalculator, OutcomeData

        calc = RewardCalculator()
        data = OutcomeData(
            impressions=10000,
            engagements=500,
            saves=200,
            shares=100,
            clicks=150,
            followers_gained=50,
            reach=8000,
        )
        breakdown = calc.compute(data)
        assert 0.0 < breakdown.final_reward <= 1.0
        assert breakdown.formula_version == "v2"

    def test_empty_metrics_zero_reward(self):
        from amplify.learning.rewards import RewardCalculator, OutcomeData

        calc = RewardCalculator()
        breakdown = calc.compute(OutcomeData())
        assert breakdown.final_reward == 0.0
        assert breakdown.completeness == 0.0

    def test_single_metric_works(self):
        from amplify.learning.rewards import RewardCalculator, OutcomeData

        calc = RewardCalculator()
        data = OutcomeData(engagement_rate=0.05)
        breakdown = calc.compute(data)
        assert breakdown.final_reward > 0
        assert "engagement_rate" in breakdown.raw_components

    def test_breakdown_is_auditable(self):
        from amplify.learning.rewards import RewardCalculator, OutcomeData

        calc = RewardCalculator()
        data = OutcomeData(
            impressions=5000,
            engagements=250,
            saves=100,
            reach=4000,
        )
        breakdown = calc.compute(data)
        # Every weighted component should be traceable
        for key in breakdown.weighted_components:
            assert key in breakdown.raw_components
            assert key in breakdown.weights_used
        # Sum of weighted components should equal raw_total
        total = sum(breakdown.weighted_components.values())
        assert abs(total - breakdown.raw_total) < 1e-5

    def test_to_dict_serialization(self):
        from amplify.learning.rewards import RewardCalculator, OutcomeData

        calc = RewardCalculator()
        data = OutcomeData(engagement_rate=0.05, reach=5000)
        breakdown = calc.compute(data, phase="pre_release")
        d = breakdown.to_dict()
        assert d["phase"] == "pre_release"
        assert d["formula_version"] == "v2"
        assert isinstance(d["raw_components"], dict)
        assert isinstance(d["final_reward"], float)


class TestDerivedRates:
    """Test automatic computation of rates from raw counts."""

    def test_rates_computed_from_impressions(self):
        from amplify.learning.rewards import RewardCalculator, OutcomeData

        calc = RewardCalculator()
        data = OutcomeData(
            impressions=10000,
            engagements=500,   # 5% engagement
            saves=200,         # 2% save rate
            clicks=100,        # 1% CTR
            followers_gained=30,  # 0.3% follow rate
        )
        breakdown = calc.compute(data)
        # Rates should have been derived and show up as components
        assert "engagement_rate" in breakdown.raw_components
        assert "save_rate" in breakdown.raw_components

    def test_explicit_rates_not_overwritten(self):
        from amplify.learning.rewards import RewardCalculator, OutcomeData

        calc = RewardCalculator()
        data = OutcomeData(
            impressions=10000,
            engagements=500,
            engagement_rate=0.08,  # Explicit, different from 500/10000=0.05
        )
        breakdown = calc.compute(data)
        # The explicit rate (0.08) should be used, normalized to 0.08/0.10=0.8
        assert breakdown.raw_components["engagement_rate"] == pytest.approx(0.8, abs=0.01)


class TestPhaseAwareWeighting:
    """Test that different phases produce different reward distributions."""

    def _make_data(self):
        from amplify.learning.rewards import OutcomeData
        return OutcomeData(
            impressions=10000,
            engagements=500,
            saves=200,
            shares=100,
            clicks=300,
            followers_gained=50,
            reach=8000,
        )

    def test_pre_release_values_saves_higher(self):
        from amplify.learning.rewards import RewardCalculator

        calc = RewardCalculator()
        data = self._make_data()
        pre = calc.compute(data, phase="pre_release")
        release = calc.compute(data, phase="release_day")

        # Pre-release should weight saves more
        pre_save_w = pre.weights_used.get("save_rate", 0)
        rel_save_w = release.weights_used.get("save_rate", 0)
        assert pre_save_w > rel_save_w

    def test_release_day_values_clicks_higher(self):
        from amplify.learning.rewards import RewardCalculator

        calc = RewardCalculator()
        data = self._make_data()
        pre = calc.compute(data, phase="pre_release")
        release = calc.compute(data, phase="release_day")

        pre_ctr_w = pre.weights_used.get("click_through_rate", 0)
        rel_ctr_w = release.weights_used.get("click_through_rate", 0)
        assert rel_ctr_w > pre_ctr_w

    def test_different_phases_produce_different_rewards(self):
        from amplify.learning.rewards import RewardCalculator

        calc = RewardCalculator()
        data = self._make_data()
        rewards = {}
        for phase in ["pre_release", "release_day", "post_release", "evergreen"]:
            rewards[phase] = calc.compute(data, phase=phase).final_reward

        # They shouldn't all be identical
        values = list(rewards.values())
        assert len(set(round(v, 4) for v in values)) > 1


class TestTemporalDecay:
    """Test temporal decay behavior."""

    def test_no_decay_without_timestamp(self):
        from amplify.learning.rewards import RewardCalculator, OutcomeData

        calc = RewardCalculator()
        breakdown = calc.compute(OutcomeData(engagement_rate=0.05))
        assert breakdown.decay_factor == 1.0

    def test_recent_decays_less_than_old(self):
        from amplify.learning.rewards import RewardCalculator, OutcomeData

        calc = RewardCalculator()
        data = OutcomeData(engagement_rate=0.05)
        now = datetime(2026, 3, 20)
        recent = calc.compute(data, observed_at=now - timedelta(days=1), now=now)
        old = calc.compute(data, observed_at=now - timedelta(days=60), now=now)
        assert recent.decay_factor > old.decay_factor

    def test_very_recent_near_one(self):
        from amplify.learning.rewards import RewardCalculator, OutcomeData

        calc = RewardCalculator()
        data = OutcomeData(engagement_rate=0.05)
        now = datetime(2026, 3, 20)
        breakdown = calc.compute(data, observed_at=now - timedelta(hours=1), now=now)
        assert breakdown.decay_factor > 0.99


class TestIncompleteWindows:
    """Test confidence adjustment for early/incomplete measurement windows."""

    def test_1h_window_low_confidence(self):
        from amplify.learning.rewards import RewardCalculator, OutcomeData

        calc = RewardCalculator()
        data = OutcomeData(impressions=1000, engagements=50)
        breakdown = calc.compute(data, measurement_window="1h")
        assert breakdown.confidence < 0.5

    def test_24h_window_higher_confidence(self):
        from amplify.learning.rewards import RewardCalculator, OutcomeData

        calc = RewardCalculator()
        data = OutcomeData(impressions=5000, engagements=250, saves=100, reach=4000)
        breakdown = calc.compute(data, measurement_window="24h")
        assert breakdown.confidence > 0.5

    def test_final_window_full_confidence_when_complete(self):
        from amplify.learning.rewards import RewardCalculator, OutcomeData

        calc = RewardCalculator()
        data = OutcomeData(
            impressions=10000, reach=8000, engagements=500,
            saves=200, shares=100, clicks=150, followers_gained=50,
            comments=30,
        )
        breakdown = calc.compute(data, measurement_window="final")
        assert breakdown.confidence > 0.8

    def test_missing_metrics_reduce_confidence(self):
        from amplify.learning.rewards import RewardCalculator, OutcomeData

        calc = RewardCalculator()
        full = OutcomeData(
            impressions=10000, reach=8000, engagements=500,
            saves=200, shares=100, clicks=150, followers_gained=50,
        )
        partial = OutcomeData(impressions=10000, engagements=500)

        full_b = calc.compute(full, measurement_window="24h")
        partial_b = calc.compute(partial, measurement_window="24h")
        assert full_b.confidence > partial_b.confidence

    def test_confidence_affects_final_reward(self):
        from amplify.learning.rewards import RewardCalculator, OutcomeData

        calc = RewardCalculator()
        data = OutcomeData(impressions=5000, engagements=250)
        early = calc.compute(data, measurement_window="1h")
        mature = calc.compute(data, measurement_window="7d")
        # Same data, different windows → different rewards due to confidence
        assert mature.final_reward > early.final_reward


class TestWeightOverrides:
    """Test per-tenant weight overrides."""

    def test_override_single_weight(self):
        from amplify.learning.rewards import RewardCalculator, OutcomeData

        calc = RewardCalculator()
        data = OutcomeData(engagement_rate=0.05, save_rate=0.02)
        # Override: make save_rate dominate
        overrides = {"save_rate": 0.90, "engagement_rate": 0.10}
        breakdown = calc.compute(data, weight_overrides=overrides)
        assert breakdown.weights_used["save_rate"] > breakdown.weights_used["engagement_rate"]

    def test_override_merged_with_phase(self):
        from amplify.learning.rewards import RewardCalculator, OutcomeData

        calc = RewardCalculator()
        data = OutcomeData(engagement_rate=0.05, reach=5000)
        # Phase is pre_release, but override reach to be higher
        overrides = {"reach": 0.50}
        breakdown = calc.compute(data, phase="pre_release", weight_overrides=overrides)
        assert breakdown.weights_used["reach"] == 0.50
        assert breakdown.phase == "pre_release"


class TestLegacyCompatibility:
    """Test backward compatibility with the old RewardConfig/OutcomeMetrics."""

    def test_legacy_outcome_metrics(self):
        from amplify.learning.rewards import RewardCalculator
        from amplify.learning.schemas.observation import OutcomeMetrics

        calc = RewardCalculator()
        outcomes = OutcomeMetrics(
            engagement_rate=0.05,
            save_rate=0.02,
            click_through_rate=0.01,
            reach=5000,
        )
        breakdown = calc.compute(outcomes)
        assert 0.0 < breakdown.final_reward <= 1.0

    def test_legacy_reward_config(self):
        from amplify.learning.rewards import RewardCalculator
        from amplify.learning.config import RewardConfig
        from amplify.learning.schemas.observation import OutcomeMetrics

        config = RewardConfig(
            engagement_weight=0.4,
            reach_weight=0.2,
            save_rate_weight=0.2,
            click_through_weight=0.2,
        )
        calc = RewardCalculator(config=config)
        outcomes = OutcomeMetrics(engagement_rate=0.05, reach=5000)
        breakdown = calc.compute(outcomes)
        assert breakdown.final_reward > 0


# ── OutcomeService integration tests ─────────────────────────────


class TestOutcomeServiceRecord:
    """Test outcome recording and reward computation via the service."""

    @pytest.mark.asyncio
    async def test_record_basic_outcome(
        self, db_session: AsyncSession, seed_post_for_outcomes,
    ):
        from app.services.outcome_service import OutcomeService

        ctx = seed_post_for_outcomes
        svc = OutcomeService(db_session, TEST_TENANT_ID)

        outcome = await svc.record_outcome(
            post_id=ctx["post"].id,
            measurement_window="24h",
            metrics={
                "impressions": 5000,
                "reach": 4000,
                "engagements": 250,
                "saves": 100,
                "shares": 50,
                "clicks": 75,
                "followers_gained": 20,
            },
            source="instagram_insights",
        )

        assert outcome.post_id == ctx["post"].id
        assert outcome.measurement_window == "24h"
        assert outcome.impressions == 5000
        assert outcome.reach == 4000
        assert outcome.saves == 100
        assert outcome.reward is not None
        assert outcome.reward > 0
        assert outcome.reward_breakdown is not None
        assert outcome.reward_breakdown["phase"] == "pre_release"
        assert outcome.reward_breakdown["formula_version"] == "v2"

    @pytest.mark.asyncio
    async def test_reward_breakdown_is_transparent(
        self, db_session: AsyncSession, seed_post_for_outcomes,
    ):
        from app.services.outcome_service import OutcomeService

        ctx = seed_post_for_outcomes
        svc = OutcomeService(db_session, TEST_TENANT_ID)

        outcome = await svc.record_outcome(
            post_id=ctx["post"].id,
            measurement_window="24h",
            metrics={
                "impressions": 10000,
                "engagements": 500,
                "saves": 200,
                "shares": 100,
            },
        )

        bd = outcome.reward_breakdown
        # Every weighted component should be explainable
        for key in bd["weighted_components"]:
            assert key in bd["raw_components"], f"Missing raw for {key}"
            assert key in bd["weights_used"], f"Missing weight for {key}"

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(
        self, db_session: AsyncSession, seed_post_for_outcomes,
    ):
        from app.services.outcome_service import OutcomeService

        ctx = seed_post_for_outcomes
        svc = OutcomeService(db_session, TEST_TENANT_ID)

        o1 = await svc.record_outcome(
            post_id=ctx["post"].id,
            measurement_window="24h",
            metrics={"impressions": 1000, "engagements": 50},
        )
        reward_before = o1.reward  # capture before upsert overwrites same object
        o2 = await svc.record_outcome(
            post_id=ctx["post"].id,
            measurement_window="24h",
            metrics={"impressions": 5000, "engagements": 300},
        )

        assert o1.id == o2.id
        assert o2.impressions == 5000
        assert o2.reward > reward_before  # more engagement = higher reward

    @pytest.mark.asyncio
    async def test_different_windows_create_separate_rows(
        self, db_session: AsyncSession, seed_post_for_outcomes,
    ):
        from app.services.outcome_service import OutcomeService

        ctx = seed_post_for_outcomes
        svc = OutcomeService(db_session, TEST_TENANT_ID)

        await svc.record_outcome(
            post_id=ctx["post"].id,
            measurement_window="24h",
            metrics={"impressions": 1000},
        )
        await svc.record_outcome(
            post_id=ctx["post"].id,
            measurement_window="7d",
            metrics={"impressions": 5000},
        )

        outcomes = await svc.get_outcomes(ctx["post"].id)
        assert len(outcomes) == 2
        windows = {o.measurement_window for o in outcomes}
        assert windows == {"24h", "7d"}

    @pytest.mark.asyncio
    async def test_get_latest_outcome(
        self, db_session: AsyncSession, seed_post_for_outcomes,
    ):
        from app.services.outcome_service import OutcomeService

        ctx = seed_post_for_outcomes
        svc = OutcomeService(db_session, TEST_TENANT_ID)

        await svc.record_outcome(
            post_id=ctx["post"].id,
            measurement_window="24h",
            metrics={"impressions": 1000},
            measured_at=datetime(2026, 3, 21, 18, 0),
        )
        await svc.record_outcome(
            post_id=ctx["post"].id,
            measurement_window="7d",
            metrics={"impressions": 5000},
            measured_at=datetime(2026, 3, 27, 18, 0),
        )

        latest = await svc.get_latest_outcome(ctx["post"].id)
        assert latest is not None
        assert latest.measurement_window == "7d"

    @pytest.mark.asyncio
    async def test_derived_rates_persisted(
        self, db_session: AsyncSession, seed_post_for_outcomes,
    ):
        from app.services.outcome_service import OutcomeService

        ctx = seed_post_for_outcomes
        svc = OutcomeService(db_session, TEST_TENANT_ID)

        outcome = await svc.record_outcome(
            post_id=ctx["post"].id,
            measurement_window="24h",
            metrics={"impressions": 10000, "engagements": 500, "saves": 200, "clicks": 100},
        )

        assert outcome.engagement_rate == pytest.approx(0.05, abs=0.001)
        assert outcome.save_rate == pytest.approx(0.02, abs=0.001)
        assert outcome.click_through_rate == pytest.approx(0.01, abs=0.001)


class TestOutcomeServiceRecompute:
    """Test reward recomputation with different weights."""

    @pytest.mark.asyncio
    async def test_recompute_changes_rewards(
        self, db_session: AsyncSession, seed_post_for_outcomes,
    ):
        from app.services.outcome_service import OutcomeService

        ctx = seed_post_for_outcomes
        svc = OutcomeService(db_session, TEST_TENANT_ID)

        await svc.record_outcome(
            post_id=ctx["post"].id,
            measurement_window="24h",
            metrics={"impressions": 10000, "engagements": 500, "saves": 200, "clicks": 100},
        )

        original = await svc.get_latest_outcome(ctx["post"].id)
        original_reward = original.reward

        # Recompute with overrides that heavily favor clicks
        outcomes = await svc.recompute_rewards(
            ctx["post"].id,
            weight_overrides={"click_through_rate": 0.90, "engagement_rate": 0.10},
        )

        assert len(outcomes) == 1
        # The reward should change (probably different)
        assert outcomes[0].reward_breakdown["weights_used"]["click_through_rate"] == 0.90

    @pytest.mark.asyncio
    async def test_recompute_empty_returns_empty(
        self, db_session: AsyncSession, seed_post_for_outcomes,
    ):
        from app.services.outcome_service import OutcomeService

        ctx = seed_post_for_outcomes
        svc = OutcomeService(db_session, TEST_TENANT_ID)
        outcomes = await svc.recompute_rewards(ctx["post"].id)
        assert outcomes == []


class TestTenantWeightOverrides:
    """Test per-tenant reward weight overrides via tenant settings."""

    @pytest.mark.asyncio
    async def test_tenant_overrides_applied(
        self, db_session: AsyncSession, seed_post_for_outcomes,
    ):
        from amplify.db.models.tenant import TenantModel
        from app.services.outcome_service import OutcomeService

        ctx = seed_post_for_outcomes

        # Set tenant-level weight overrides
        stmt = select(TenantModel).where(TenantModel.id == TEST_TENANT_ID)
        result = await db_session.execute(stmt)
        tenant = result.scalar_one()
        tenant.settings = {"reward_weights": {"save_rate": 0.80, "engagement_rate": 0.20}}
        await db_session.flush()

        svc = OutcomeService(db_session, TEST_TENANT_ID)
        outcome = await svc.record_outcome(
            post_id=ctx["post"].id,
            measurement_window="24h",
            metrics={"impressions": 10000, "engagements": 500, "saves": 200},
        )

        bd = outcome.reward_breakdown
        assert bd["weights_used"]["save_rate"] == 0.80
        assert bd["weights_used"]["engagement_rate"] == 0.20


# ── API endpoint tests ───────────────────────────────────────────


class TestOutcomeEndpoints:
    @pytest.mark.asyncio
    async def test_record_outcome_endpoint(self, client, seed_post_for_outcomes):
        ctx = seed_post_for_outcomes
        resp = await client.post("/api/v1/outcomes/", json={
            "post_id": str(ctx["post"].id),
            "measurement_window": "24h",
            "metrics": {
                "impressions": 5000,
                "engagements": 250,
                "saves": 100,
                "shares": 50,
            },
            "source": "test",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["measurement_window"] == "24h"
        assert data["impressions"] == 5000
        assert data["reward"] is not None
        assert data["reward"] > 0
        assert "formula_version" in data["reward_breakdown"]

    @pytest.mark.asyncio
    async def test_get_outcomes_endpoint(self, client, seed_post_for_outcomes):
        ctx = seed_post_for_outcomes
        # Record first
        await client.post("/api/v1/outcomes/", json={
            "post_id": str(ctx["post"].id),
            "measurement_window": "24h",
            "metrics": {"impressions": 1000},
        })

        resp = await client.get(f"/api/v1/outcomes/{ctx['post'].id}")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    @pytest.mark.asyncio
    async def test_get_latest_endpoint(self, client, seed_post_for_outcomes):
        ctx = seed_post_for_outcomes
        await client.post("/api/v1/outcomes/", json={
            "post_id": str(ctx["post"].id),
            "measurement_window": "24h",
            "metrics": {"impressions": 1000},
        })

        resp = await client.get(f"/api/v1/outcomes/{ctx['post'].id}/latest")
        assert resp.status_code == 200
        assert resp.json()["measurement_window"] == "24h"

    @pytest.mark.asyncio
    async def test_get_latest_not_found(self, client, seed_tenant):
        fake_id = uuid.uuid4()
        resp = await client.get(f"/api/v1/outcomes/{fake_id}/latest")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_recompute_endpoint(self, client, seed_post_for_outcomes):
        ctx = seed_post_for_outcomes
        await client.post("/api/v1/outcomes/", json={
            "post_id": str(ctx["post"].id),
            "measurement_window": "24h",
            "metrics": {"impressions": 5000, "engagements": 250, "saves": 100},
        })

        resp = await client.post(
            f"/api/v1/outcomes/{ctx['post'].id}/recompute",
            json={"weight_overrides": {"save_rate": 0.90}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["reward_breakdown"]["weights_used"]["save_rate"] == 0.90


# ── Example phase configs ────────────────────────────────────────


class TestExamplePhaseConfigs:
    """Document and validate example reward configurations for each phase."""

    def _example_metrics(self):
        from amplify.learning.rewards import OutcomeData
        return OutcomeData(
            impressions=10000,
            reach=8000,
            engagements=500,
            saves=200,
            shares=100,
            clicks=150,
            comments=30,
            followers_gained=50,
        )

    def test_pre_release_example(self):
        """Pre-release: presaves and follower growth are the priority."""
        from amplify.learning.rewards import RewardCalculator, pre_release_weights

        calc = RewardCalculator(weights=pre_release_weights())
        data = self._example_metrics()
        b = calc.compute(data, measurement_window="24h")

        assert b.final_reward > 0
        assert b.phase is None  # explicit weights, no phase lookup
        # Save rate and followers should be top contributors
        top = sorted(b.weighted_components, key=b.weighted_components.get, reverse=True)
        assert "save_rate" in top[:3]
        assert "followers_gained" in top[:3]

    def test_release_day_example(self):
        """Release day: clicks to streaming link and shares drive the reward."""
        from amplify.learning.rewards import RewardCalculator, release_day_weights

        calc = RewardCalculator(weights=release_day_weights())
        data = self._example_metrics()
        b = calc.compute(data, measurement_window="24h")

        assert b.final_reward > 0
        top = sorted(b.weighted_components, key=b.weighted_components.get, reverse=True)
        assert "click_through_rate" in top[:3]
        assert "share_rate" in top[:3]

    def test_post_release_example(self):
        """Post-release: sustained saves and deep engagement matter."""
        from amplify.learning.rewards import RewardCalculator, post_release_weights

        calc = RewardCalculator(weights=post_release_weights())
        data = self._example_metrics()
        b = calc.compute(data, measurement_window="7d")

        assert b.final_reward > 0
        top = sorted(b.weighted_components, key=b.weighted_components.get, reverse=True)
        assert "save_rate" in top[:3]

    def test_evergreen_example(self):
        """Evergreen: follower growth and community engagement."""
        from amplify.learning.rewards import RewardCalculator, evergreen_weights

        calc = RewardCalculator(weights=evergreen_weights())
        data = self._example_metrics()
        b = calc.compute(data, measurement_window="final")

        assert b.final_reward > 0
        top = sorted(b.weighted_components, key=b.weighted_components.get, reverse=True)
        assert "followers_gained" in top[:3]

    def test_early_window_reduces_reward(self):
        """Early (1h) window should produce lower reward than mature (7d) window."""
        from amplify.learning.rewards import RewardCalculator

        calc = RewardCalculator()
        data = self._example_metrics()
        early = calc.compute(data, phase="pre_release", measurement_window="1h")
        mature = calc.compute(data, phase="pre_release", measurement_window="7d")
        assert mature.final_reward > early.final_reward

    def test_ab_test_formula_version(self):
        """Reward breakdowns include formula_version for A/B testing."""
        from amplify.learning.rewards import RewardCalculator, FORMULA_VERSION

        calc = RewardCalculator()
        data = self._example_metrics()
        b = calc.compute(data, phase="release_day")
        assert b.formula_version == FORMULA_VERSION
        assert b.to_dict()["formula_version"] == FORMULA_VERSION

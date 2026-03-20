"""Tests for the intelligence dashboard service."""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

_api_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_api_root))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///")
os.environ.setdefault("JWT_SECRET", "test-secret")

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker


# ── DB fixtures ──────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    from amplify.db.base import Base
    import amplify.db.models  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


TENANT_ID = uuid.uuid4()


@pytest_asyncio.fixture
async def tenant(db_session):
    from amplify.db.models.tenant import TenantModel
    t = TenantModel(
        id=TENANT_ID,
        name="Test Tenant",
        slug="test-tenant",
        settings={},
    )
    db_session.add(t)
    await db_session.flush()
    return t


# ── Helpers ──────────────────────────────────────────────────────


def _get_service(db_session):
    from app.services.intelligence_dashboard_service import IntelligenceDashboardService
    return IntelligenceDashboardService(db_session, TENANT_ID)


async def _seed_patterns(db_session, count=5, low_confidence_count=2):
    from amplify.db.models.learning import TenantPatternModel
    now = datetime.now(timezone.utc)
    patterns = []
    for i in range(count):
        p = TenantPatternModel(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            pattern_type="timing",
            subject_feature="post_hour",
            subject_value=str(10 + i),
            title=f"Post at {10 + i}:00",
            explanation=f"Posts at {10 + i}:00 perform well",
            confidence=0.8 if i >= low_confidence_count else 0.2,
            direction="winning",
            supporting_observation_count=50 if i >= low_confidence_count else 3,
            source="tenant",
            is_active=True,
            version=1,
        )
        db_session.add(p)
        patterns.append(p)
    await db_session.flush()
    return patterns


async def _seed_global_priors(db_session, count=3):
    from amplify.db.models.learning import GlobalPatternStatModel
    now = datetime.now(timezone.utc)
    for i in range(count):
        p = GlobalPatternStatModel(
            id=uuid.uuid4(),
            feature_name="post_hour",
            feature_value=str(18 + i),
            action_type="",
            mean_reward=0.6 + i * 0.05,
            std_dev=0.1,
            confidence_lower=0.5,
            confidence_upper=0.7,
            observation_count=100 + i * 20,
            tenant_count=10 + i,
            k_anonymity_threshold=5,
            category="posting_window",
            computed_at=now,
            version=1,
            is_active=True,
        )
        db_session.add(p)
    await db_session.flush()


async def _seed_outcomes(db_session, count=10):
    from amplify.db.models.learning import PostOutcomeModel
    now = datetime.now(timezone.utc)
    for i in range(count):
        o = PostOutcomeModel(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            post_id=uuid.uuid4(),
            measurement_window="24h",
            reward=0.3 + (i * 0.05),
            measured_at=now - timedelta(days=count - i),
        )
        db_session.add(o)
    await db_session.flush()


async def _seed_feature_vectors(db_session, count=5):
    from amplify.db.models.learning import PostFeatureVectorModel
    now = datetime.now(timezone.utc)
    post_ids = []
    for i in range(count):
        pid = uuid.uuid4()
        post_ids.append(pid)
        fv = PostFeatureVectorModel(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            post_id=pid,
            features={"post_hour": str(18 + i % 3), "cta_type": "link"},
            platform="instagram",
            post_hour=18 + i % 3,
            extracted_at=now,
        )
        db_session.add(fv)
    await db_session.flush()
    return post_ids


async def _seed_events(db_session, post_ids=None, count=5):
    from amplify.db.models.learning import LearningEventModel
    now = datetime.now(timezone.utc)
    for i in range(count):
        e = LearningEventModel(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            action_type="post_published",
            post_id=post_ids[i % len(post_ids)] if post_ids else uuid.uuid4(),
            features={"post_hour": "18"},
            outcomes={"engagement_rate": 0.05},
            reward=0.5 + i * 0.05,
            observed_at=now - timedelta(hours=count - i),
            source="test",
        )
        db_session.add(e)
    await db_session.flush()


async def _seed_audit_log(db_session, count=5):
    from amplify.db.models.learning import LearningAuditLogModel
    now = datetime.now(timezone.utc)
    for i in range(count):
        entry = LearningAuditLogModel(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            action="ranking_produced" if i % 2 == 0 else "pattern_updated",
            entity_type="pattern",
            entity_id=uuid.uuid4(),
            details={"confidence": 0.1 if i == 0 else 0.8, "sample_size": 2 if i == 0 else 50},
            explanation=f"Test entry {i}",
        )
        db_session.add(entry)
    await db_session.flush()


# ── Tests: Overview ──────────────────────────────────────────────


class TestOverview:
    @pytest.mark.asyncio
    async def test_overview_empty(self, db_session, tenant):
        svc = _get_service(db_session)
        result = await svc.get_overview()
        assert result["active_patterns"] == 0
        assert result["feature_vectors"] == 0
        assert result["outcomes"] == 0
        assert result["learning_events"] == 0

    @pytest.mark.asyncio
    async def test_overview_with_data(self, db_session, tenant):
        await _seed_patterns(db_session, count=5, low_confidence_count=2)
        await _seed_outcomes(db_session)
        post_ids = await _seed_feature_vectors(db_session)
        await _seed_events(db_session, post_ids)
        await _seed_global_priors(db_session)

        svc = _get_service(db_session)
        result = await svc.get_overview()
        assert result["active_patterns"] == 5
        assert result["feature_vectors"] == 5
        assert result["outcomes"] == 10
        assert result["learning_events"] == 5
        assert result["global_priors_available"] == 3
        assert result["low_confidence_patterns"] == 2


# ── Tests: Tenant Patterns ───────────────────────────────────────


class TestTenantPatterns:
    @pytest.mark.asyncio
    async def test_list_patterns(self, db_session, tenant):
        await _seed_patterns(db_session)
        svc = _get_service(db_session)
        result = await svc.get_tenant_patterns()
        assert result["total"] == 5
        assert len(result["patterns"]) == 5

    @pytest.mark.asyncio
    async def test_filter_by_type(self, db_session, tenant):
        await _seed_patterns(db_session)
        svc = _get_service(db_session)
        result = await svc.get_tenant_patterns(pattern_type="timing")
        assert all(p["pattern_type"] == "timing" for p in result["patterns"])

    @pytest.mark.asyncio
    async def test_min_confidence_filter(self, db_session, tenant):
        await _seed_patterns(db_session, count=5, low_confidence_count=2)
        svc = _get_service(db_session)
        result = await svc.get_tenant_patterns(min_confidence=0.5)
        assert all(p["confidence"] >= 0.5 for p in result["patterns"])

    @pytest.mark.asyncio
    async def test_pattern_has_evidence_fields(self, db_session, tenant):
        await _seed_patterns(db_session)
        svc = _get_service(db_session)
        result = await svc.get_tenant_patterns()
        for p in result["patterns"]:
            assert "confidence" in p
            assert "supporting_observation_count" in p
            assert "evidence" in p
            assert "source" in p
            assert "direction" in p


# ── Tests: Global Priors ─────────────────────────────────────────


class TestGlobalPriors:
    @pytest.mark.asyncio
    async def test_list_priors(self, db_session, tenant):
        await _seed_global_priors(db_session)
        svc = _get_service(db_session)
        result = await svc.get_global_priors()
        assert result["total"] == 3
        assert len(result["priors"]) == 3

    @pytest.mark.asyncio
    async def test_filter_by_category(self, db_session, tenant):
        await _seed_global_priors(db_session)
        svc = _get_service(db_session)
        result = await svc.get_global_priors(category="posting_window")
        assert all(p["category"] == "posting_window" for p in result["priors"])

    @pytest.mark.asyncio
    async def test_prior_has_sample_size(self, db_session, tenant):
        await _seed_global_priors(db_session)
        svc = _get_service(db_session)
        result = await svc.get_global_priors()
        for p in result["priors"]:
            assert "observation_count" in p
            assert "tenant_count" in p
            assert "mean_reward" in p
            assert "confidence_lower" in p
            assert "confidence_upper" in p

    @pytest.mark.asyncio
    async def test_min_tenant_count_filter(self, db_session, tenant):
        await _seed_global_priors(db_session)
        svc = _get_service(db_session)
        result = await svc.get_global_priors(min_tenant_count=12)
        assert all(p["tenant_count"] >= 12 for p in result["priors"])


# ── Tests: Reward Trends ─────────────────────────────────────────


class TestRewardTrends:
    @pytest.mark.asyncio
    async def test_reward_trends(self, db_session, tenant):
        await _seed_outcomes(db_session, count=10)
        svc = _get_service(db_session)
        result = await svc.get_reward_trends(days=30)
        assert "trend" in result
        # Each point has required fields
        for p in result["trend"]:
            assert "date" in p
            assert "avg_reward" in p
            assert "count" in p
            assert "min_reward" in p
            assert "max_reward" in p

    @pytest.mark.asyncio
    async def test_empty_trends(self, db_session, tenant):
        svc = _get_service(db_session)
        result = await svc.get_reward_trends(days=7)
        assert result["trend"] == []


# ── Tests: Post Drill-Down ───────────────────────────────────────


class TestPostDrillDown:
    @pytest.mark.asyncio
    async def test_post_intelligence(self, db_session, tenant):
        post_ids = await _seed_feature_vectors(db_session, count=1)
        pid = post_ids[0]
        await _seed_events(db_session, post_ids=[pid], count=2)

        # Seed an outcome for this post
        from amplify.db.models.learning import PostOutcomeModel
        o = PostOutcomeModel(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            post_id=pid,
            measurement_window="24h",
            reward=0.7,
            measured_at=datetime.now(timezone.utc),
        )
        db_session.add(o)
        await db_session.flush()

        svc = _get_service(db_session)
        result = await svc.get_post_intelligence(pid)

        assert result["post_id"] == str(pid)
        assert result["features"] is not None
        assert result["features"]["post_id"] == str(pid)
        assert len(result["outcomes"]) == 1
        assert len(result["learning_events"]) == 2

    @pytest.mark.asyncio
    async def test_post_with_matching_patterns(self, db_session, tenant):
        """Post features match stored patterns."""
        post_ids = await _seed_feature_vectors(db_session, count=1)
        pid = post_ids[0]
        await _seed_patterns(db_session)
        await _seed_global_priors(db_session)

        svc = _get_service(db_session)
        result = await svc.get_post_intelligence(pid)

        # The feature vector has post_hour=18, and we seeded patterns/priors for post_hour=18
        assert "matching_patterns" in result
        assert "matching_global_priors" in result

    @pytest.mark.asyncio
    async def test_nonexistent_post(self, db_session, tenant):
        svc = _get_service(db_session)
        result = await svc.get_post_intelligence(uuid.uuid4())
        assert result["features"] is None
        assert result["outcomes"] == []
        assert result["learning_events"] == []


# ── Tests: Evaluation Summary ────────────────────────────────────


class TestEvaluationSummary:
    @pytest.mark.asyncio
    async def test_evaluation_runs(self, db_session, tenant):
        from amplify.db.models.learning import EvaluationRunModel
        now = datetime.now(timezone.utc)
        run = EvaluationRunModel(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            evaluation_type="ranking",
            scope="tenant",
            window_start=now - timedelta(days=7),
            window_end=now,
            sample_size=50,
            mae=0.15,
            rank_correlation=0.72,
            top_k_precision=0.8,
            status="completed",
        )
        db_session.add(run)
        await db_session.flush()

        svc = _get_service(db_session)
        result = await svc.get_evaluation_summary()
        assert result["total"] == 1
        assert result["runs"][0]["mae"] == 0.15
        assert result["runs"][0]["rank_correlation"] == 0.72
        assert result["runs"][0]["top_k_precision"] == 0.8

    @pytest.mark.asyncio
    async def test_empty_evaluations(self, db_session, tenant):
        svc = _get_service(db_session)
        result = await svc.get_evaluation_summary()
        assert result["runs"] == []


# ── Tests: Suspicious Actions ────────────────────────────────────


class TestSuspiciousActions:
    @pytest.mark.asyncio
    async def test_low_confidence_flagged(self, db_session, tenant):
        await _seed_patterns(db_session, count=5, low_confidence_count=2)
        svc = _get_service(db_session)
        result = await svc.get_suspicious_actions(days=7)
        low_conf = [a for a in result["alerts"] if a["type"] == "low_confidence_pattern"]
        assert len(low_conf) == 2

    @pytest.mark.asyncio
    async def test_thin_data_high_confidence_flagged(self, db_session, tenant):
        from amplify.db.models.learning import TenantPatternModel
        p = TenantPatternModel(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            pattern_type="timing",
            subject_feature="post_hour",
            subject_value="22",
            title="Post at 22:00",
            explanation="Overfitted?",
            confidence=0.9,
            direction="winning",
            supporting_observation_count=2,
            source="tenant",
            is_active=True,
            version=1,
        )
        db_session.add(p)
        await db_session.flush()

        svc = _get_service(db_session)
        result = await svc.get_suspicious_actions(days=7)
        thin = [a for a in result["alerts"] if a["type"] == "thin_data_high_confidence"]
        assert len(thin) == 1

    @pytest.mark.asyncio
    async def test_anomalous_reward_flagged(self, db_session, tenant):
        from amplify.db.models.learning import PostOutcomeModel
        o = PostOutcomeModel(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            post_id=uuid.uuid4(),
            measurement_window="24h",
            reward=1.5,  # outside 0-1
            measured_at=datetime.now(timezone.utc),
        )
        db_session.add(o)
        await db_session.flush()

        svc = _get_service(db_session)
        result = await svc.get_suspicious_actions(days=7)
        anomalous = [a for a in result["alerts"] if a["type"] == "anomalous_reward"]
        assert len(anomalous) == 1

    @pytest.mark.asyncio
    async def test_no_alerts_when_healthy(self, db_session, tenant):
        svc = _get_service(db_session)
        result = await svc.get_suspicious_actions(days=7)
        assert result["alerts"] == []

    @pytest.mark.asyncio
    async def test_alerts_sorted_by_severity(self, db_session, tenant):
        await _seed_patterns(db_session, count=5, low_confidence_count=2)
        # Also add anomalous reward
        from amplify.db.models.learning import PostOutcomeModel
        o = PostOutcomeModel(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            post_id=uuid.uuid4(),
            measurement_window="24h",
            reward=-0.5,
            measured_at=datetime.now(timezone.utc),
        )
        db_session.add(o)
        await db_session.flush()

        svc = _get_service(db_session)
        result = await svc.get_suspicious_actions(days=7)
        severities = [a["severity"] for a in result["alerts"]]
        severity_order = {"error": 0, "warning": 1, "info": 2}
        ranks = [severity_order.get(s, 3) for s in severities]
        assert ranks == sorted(ranks)


# ── Tests: Audit Trail ──────────────────────────────────────────


class TestAuditTrail:
    @pytest.mark.asyncio
    async def test_audit_entries(self, db_session, tenant):
        await _seed_audit_log(db_session)
        svc = _get_service(db_session)
        result = await svc.get_audit_trail()
        assert result["total"] == 5

    @pytest.mark.asyncio
    async def test_suspicious_filter(self, db_session, tenant):
        await _seed_audit_log(db_session)
        svc = _get_service(db_session)
        result = await svc.get_audit_trail(suspicious_only=True)
        # First entry has confidence=0.1, sample_size=2 → suspicious
        assert result["total"] >= 1
        for entry in result["entries"]:
            details = entry.get("details", {})
            # Must match at least one suspicious heuristic
            assert (
                details.get("confidence", 1.0) < 0.3
                or details.get("sample_size", 100) < 3
            )

    @pytest.mark.asyncio
    async def test_action_filter(self, db_session, tenant):
        await _seed_audit_log(db_session)
        svc = _get_service(db_session)
        result = await svc.get_audit_trail(action="ranking_produced")
        for entry in result["entries"]:
            assert entry["action"] == "ranking_produced"

    @pytest.mark.asyncio
    async def test_empty_audit(self, db_session, tenant):
        svc = _get_service(db_session)
        result = await svc.get_audit_trail()
        assert result["entries"] == []


# ── Tests: Ranking Decisions ─────────────────────────────────────


class TestRankingDecisions:
    @pytest.mark.asyncio
    async def test_ranking_decisions(self, db_session, tenant):
        from amplify.db.models.learning import LearningAuditLogModel
        now = datetime.now(timezone.utc)
        for action in ["ranking_produced", "bandit_selection", "exploration_decision"]:
            entry = LearningAuditLogModel(
                id=uuid.uuid4(),
                tenant_id=TENANT_ID,
                action=action,
                entity_type="action",
                details={"score": 0.8},
                explanation=f"Test {action}",
            )
            db_session.add(entry)
        await db_session.flush()

        svc = _get_service(db_session)
        result = await svc.get_ranking_decisions(days=7)
        assert result["total"] == 3

    @pytest.mark.asyncio
    async def test_empty_decisions(self, db_session, tenant):
        svc = _get_service(db_session)
        result = await svc.get_ranking_decisions(days=7)
        assert result["decisions"] == []

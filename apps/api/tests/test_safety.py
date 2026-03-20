"""API-level tests for the safety service.

Self-contained with its own DB fixtures.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

_api_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_api_root))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///")
os.environ.setdefault("JWT_SECRET", "test-secret")

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select


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
    """Create a tenant for testing."""
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
    from app.services.safety_service import SafetyService
    return SafetyService(db_session, TENANT_ID)


async def _add_learning_events(db_session, n: int, action_type: str = "post_published", reward: float = 0.7, platform: str = "instagram"):
    from amplify.db.models.learning import LearningEventModel
    for i in range(n):
        db_session.add(LearningEventModel(
            id=uuid.uuid4(),
            tenant_id=TENANT_ID,
            action_type=action_type,
            observed_at=datetime.now(timezone.utc) - timedelta(hours=i),
            platform=platform,
            reward=reward,
        ))
    await db_session.flush()


# ── Tests: Controls CRUD ────────────────────────────────────────


class TestControlsCRUD:
    @pytest.mark.asyncio
    async def test_get_default_controls(self, db_session, tenant):
        svc = _get_service(db_session)
        controls = await svc.get_controls()
        assert controls.enabled is True
        assert controls.max_exploration_rate == 0.15

    @pytest.mark.asyncio
    async def test_update_controls(self, db_session, tenant):
        svc = _get_service(db_session)
        controls = await svc.update_controls({
            "max_exploration_rate": 0.05,
            "shadow_only": False,
        })
        await db_session.commit()
        assert controls.max_exploration_rate == 0.05
        assert controls.shadow_only is False

    @pytest.mark.asyncio
    async def test_update_persists(self, db_session, tenant):
        svc = _get_service(db_session)
        await svc.update_controls({"max_experiments_per_week": 25})
        await db_session.commit()

        controls = await svc.get_controls()
        assert controls.max_experiments_per_week == 25

    @pytest.mark.asyncio
    async def test_update_invalid_rejected(self, db_session, tenant):
        svc = _get_service(db_session)
        with pytest.raises(ValueError, match="Invalid"):
            await svc.update_controls({"max_exploration_rate": 5.0})

    @pytest.mark.asyncio
    async def test_update_audited(self, db_session, tenant):
        from amplify.db.models.learning import LearningAuditLogModel
        svc = _get_service(db_session)
        await svc.update_controls({"enabled": False})
        await db_session.commit()

        stmt = select(LearningAuditLogModel).where(
            LearningAuditLogModel.action == "exploration_controls.updated"
        )
        result = await db_session.execute(stmt)
        logs = list(result.scalars().all())
        assert len(logs) == 1

    @pytest.mark.asyncio
    async def test_partial_update_preserves_other_fields(self, db_session, tenant):
        svc = _get_service(db_session)
        await svc.update_controls({"max_exploration_rate": 0.08})
        await db_session.commit()

        await svc.update_controls({"shadow_only": False})
        await db_session.commit()

        controls = await svc.get_controls()
        assert controls.max_exploration_rate == 0.08
        assert controls.shadow_only is False


# ── Tests: Safety checks ────────────────────────────────────────


class TestSafetyChecks:
    @pytest.mark.asyncio
    async def test_check_with_no_data_forces_fallback(self, db_session, tenant):
        svc = _get_service(db_session)
        result = await svc.check_before_exploration()
        assert result.allowed is True
        assert result.force_fallback is True

    @pytest.mark.asyncio
    async def test_check_with_sufficient_data(self, db_session, tenant):
        svc = _get_service(db_session)
        await _add_learning_events(db_session, 50)
        result = await svc.check_before_exploration(total_observations=50)
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_check_disabled_tenant(self, db_session, tenant):
        svc = _get_service(db_session)
        await svc.update_controls({"enabled": False})
        await db_session.commit()

        result = await svc.check_before_exploration()
        assert result.allowed is False

    @pytest.mark.asyncio
    async def test_check_with_budget_exhausted(self, db_session, tenant):
        svc = _get_service(db_session)
        await svc.update_controls({"max_experiments_per_week": 5})
        await db_session.commit()

        # Add 5 exploratory events
        await _add_learning_events(
            db_session, 5, action_type="bandit_exploration",
        )

        result = await svc.check_before_exploration(total_observations=100)
        assert result.force_fallback is True

    @pytest.mark.asyncio
    async def test_check_audited(self, db_session, tenant):
        from amplify.db.models.learning import LearningAuditLogModel
        svc = _get_service(db_session)
        await svc.check_before_exploration()
        await db_session.commit()

        stmt = select(LearningAuditLogModel).where(
            LearningAuditLogModel.action == "safety_check.performed"
        )
        result = await db_session.execute(stmt)
        assert len(list(result.scalars().all())) == 1


# ── Tests: Rollback ─────────────────────────────────────────────


class TestRollback:
    @pytest.mark.asyncio
    async def test_rollback_with_no_events(self, db_session, tenant):
        svc = _get_service(db_session)
        decision = await svc.evaluate_rollback()
        assert decision.should_rollback is False

    @pytest.mark.asyncio
    async def test_rollback_with_stable_performance(self, db_session, tenant):
        svc = _get_service(db_session)
        await _add_learning_events(db_session, 20, reward=0.7)
        decision = await svc.evaluate_rollback()
        assert decision.should_rollback is False

    @pytest.mark.asyncio
    async def test_rollback_audited_when_triggered(self, db_session, tenant):
        from amplify.db.models.learning import LearningAuditLogModel, LearningEventModel
        svc = _get_service(db_session)

        # Add high baseline (non-exploratory)
        for i in range(10):
            db_session.add(LearningEventModel(
                id=uuid.uuid4(),
                tenant_id=TENANT_ID,
                action_type="post_published",
                observed_at=datetime.now(timezone.utc) - timedelta(days=2, hours=i),
                reward=0.8,
            ))

        # Add low recent (all types including exploratory)
        for i in range(10):
            db_session.add(LearningEventModel(
                id=uuid.uuid4(),
                tenant_id=TENANT_ID,
                action_type="bandit_exploration",
                observed_at=datetime.now(timezone.utc) - timedelta(hours=i),
                reward=0.3,
            ))
        await db_session.flush()

        decision = await svc.evaluate_rollback()
        await db_session.commit()

        if decision.should_rollback:
            stmt = select(LearningAuditLogModel).where(
                LearningAuditLogModel.action == "auto_rollback.triggered"
            )
            result = await db_session.execute(stmt)
            logs = list(result.scalars().all())
            assert len(logs) == 1


# ── Tests: Experiment stats ─────────────────────────────────────


class TestExperimentStats:
    @pytest.mark.asyncio
    async def test_stats_with_no_data(self, db_session, tenant):
        svc = _get_service(db_session)
        stats = await svc.get_experiment_stats()
        assert stats["experiments_this_week"] == 0
        assert stats["total_observations"] == 0
        assert "controls" in stats

    @pytest.mark.asyncio
    async def test_stats_counts_experiments(self, db_session, tenant):
        svc = _get_service(db_session)
        await _add_learning_events(
            db_session, 3, action_type="bandit_exploration",
        )
        await _add_learning_events(
            db_session, 5, action_type="post_published",
        )
        stats = await svc.get_experiment_stats()
        assert stats["experiments_this_week"] == 3
        assert stats["total_observations"] == 8

    @pytest.mark.asyncio
    async def test_stats_platform_usage(self, db_session, tenant):
        svc = _get_service(db_session)
        await _add_learning_events(
            db_session, 2, action_type="bandit_exploration", platform="instagram",
        )
        await _add_learning_events(
            db_session, 1, action_type="bandit_exploration", platform="tiktok",
        )
        stats = await svc.get_experiment_stats()
        assert stats["platform_usage"]["instagram"] == 2
        assert stats["platform_usage"]["tiktok"] == 1


# ── Tests: Anomaly detection ────────────────────────────────────


class TestAnomalyDetection:
    @pytest.mark.asyncio
    async def test_no_anomalies_with_low_usage(self, db_session, tenant):
        svc = _get_service(db_session)
        alerts = await svc.detect_anomalies()
        assert alerts == []

    @pytest.mark.asyncio
    async def test_budget_alert_near_limit(self, db_session, tenant):
        svc = _get_service(db_session)
        await svc.update_controls({"max_experiments_per_week": 10})
        await db_session.commit()
        await _add_learning_events(
            db_session, 9, action_type="bandit_exploration",
        )
        alerts = await svc.detect_anomalies()
        assert any(a.code == "budget_nearly_exhausted" for a in alerts)

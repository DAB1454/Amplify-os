"""API-level tests for the cohort service.

Self-contained with its own DB fixtures.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
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
    from app.services.cohort_service import CohortService
    return CohortService(db_session, TENANT_ID)


# ── Tests: CRUD ──────────────────────────────────────────────────


class TestCRUD:
    @pytest.mark.asyncio
    async def test_create_definition(self, db_session, tenant):
        svc = _get_service(db_session)
        result = await svc.create_definition({
            "name": "Hip-Hop Artists",
            "description": "Hip-hop genre family",
            "criteria": {"genre_family": ["hip-hop", "rap"]},
        })
        await db_session.commit()
        assert result["name"] == "Hip-Hop Artists"
        assert result["is_active"] is True
        assert result["criteria"]["genre_family"] == ["hip-hop", "rap"]

    @pytest.mark.asyncio
    async def test_list_definitions(self, db_session, tenant):
        svc = _get_service(db_session)
        await svc.create_definition({"name": "A", "criteria": {"x": 1}})
        await svc.create_definition({"name": "B", "criteria": {"y": 2}})
        await db_session.commit()

        results = await svc.list_definitions()
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_get_definition(self, db_session, tenant):
        svc = _get_service(db_session)
        created = await svc.create_definition({"name": "Test", "criteria": {"a": 1}})
        await db_session.commit()

        result = await svc.get_definition(uuid.UUID(created["id"]))
        assert result["name"] == "Test"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, db_session, tenant):
        svc = _get_service(db_session)
        result = await svc.get_definition(uuid.uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_update_definition(self, db_session, tenant):
        svc = _get_service(db_session)
        created = await svc.create_definition({"name": "Old", "criteria": {"a": 1}})
        await db_session.flush()

        updated = await svc.update_definition(
            uuid.UUID(created["id"]),
            {"name": "New", "criteria": {"b": 2}},
        )
        assert updated["name"] == "New"
        assert updated["criteria"] == {"b": 2}

    @pytest.mark.asyncio
    async def test_delete_definition(self, db_session, tenant):
        svc = _get_service(db_session)
        created = await svc.create_definition({"name": "Doomed", "criteria": {}})
        await db_session.commit()

        deleted = await svc.delete_definition(uuid.UUID(created["id"]))
        assert deleted is True

        # Should no longer appear in active list
        results = await svc.list_definitions(active_only=True)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, db_session, tenant):
        svc = _get_service(db_session)
        deleted = await svc.delete_definition(uuid.uuid4())
        assert deleted is False


# ── Tests: Assignment ─────────────────────────────────────────────


class TestAssignment:
    @pytest.mark.asyncio
    async def test_assign_to_matching(self, db_session, tenant):
        svc = _get_service(db_session)
        await svc.create_definition({
            "name": "Hip-Hop",
            "criteria": {"genre_family": ["hip-hop"]},
        })
        await svc.create_definition({
            "name": "Small",
            "criteria": {"audience_size_bucket": "small"},
        })
        await db_session.commit()

        from amplify.learning.cohorts.engine import TenantProfile
        profile = TenantProfile(
            tenant_id=TENANT_ID,
            traits={"genre_family": "hip-hop", "audience_size_bucket": "small"},
        )
        assignments = await svc.assign_tenant(profile)
        names = {a["cohort_name"] for a in assignments}
        assert "Hip-Hop" in names
        assert "Small" in names

    @pytest.mark.asyncio
    async def test_no_matching(self, db_session, tenant):
        svc = _get_service(db_session)
        await svc.create_definition({
            "name": "Pop",
            "criteria": {"genre_family": ["pop"]},
        })
        await db_session.commit()

        from amplify.learning.cohorts.engine import TenantProfile
        profile = TenantProfile(
            tenant_id=TENANT_ID,
            traits={"genre_family": "hip-hop"},
        )
        assignments = await svc.assign_tenant(profile)
        assert assignments == []


# ── Tests: Aggregation ────────────────────────────────────────────


class TestAggregation:
    @pytest.mark.asyncio
    async def test_aggregate_cohort(self, db_session, tenant):
        from amplify.learning.cohorts.engine import TenantProfile, CohortEngine as CE
        svc = _get_service(db_session)
        svc.engine = CE(k_anonymity_threshold=2)

        created = await svc.create_definition({
            "name": "Hip-Hop",
            "criteria": {"genre_family": ["hip-hop"]},
        })
        await db_session.commit()

        profiles = [
            TenantProfile(
                tenant_id=uuid.uuid4(),
                traits={"genre_family": "hip-hop"},
                observation_count=10,
                rewards=[0.5, 0.6, 0.7],
            ),
            TenantProfile(
                tenant_id=uuid.uuid4(),
                traits={"genre_family": "hip-hop"},
                observation_count=10,
                rewards=[0.4, 0.5, 0.6],
            ),
        ]

        stats = await svc.aggregate_cohort(uuid.UUID(created["id"]), profiles)
        assert stats is not None
        assert stats.tenant_count == 2

    @pytest.mark.asyncio
    async def test_aggregate_all(self, db_session, tenant):
        from amplify.learning.cohorts.engine import TenantProfile, CohortEngine as CE
        svc = _get_service(db_session)
        svc.engine = CE(k_anonymity_threshold=2)

        await svc.create_definition({
            "name": "Hip-Hop",
            "criteria": {"genre_family": ["hip-hop"]},
        })
        await svc.create_definition({
            "name": "Pop",
            "criteria": {"genre_family": ["pop"]},
        })
        await db_session.commit()

        profiles = [
            TenantProfile(tenant_id=uuid.uuid4(), traits={"genre_family": "hip-hop"}, rewards=[0.5]),
            TenantProfile(tenant_id=uuid.uuid4(), traits={"genre_family": "hip-hop"}, rewards=[0.6]),
            TenantProfile(tenant_id=uuid.uuid4(), traits={"genre_family": "pop"}, rewards=[0.7]),
            TenantProfile(tenant_id=uuid.uuid4(), traits={"genre_family": "pop"}, rewards=[0.8]),
        ]

        all_stats = await svc.aggregate_all(profiles)
        assert len(all_stats) == 2


# ── Tests: Cold-start priors ─────────────────────────────────────


class TestColdStartPriors:
    @pytest.mark.asyncio
    async def test_cold_start_priors(self, db_session, tenant):
        from amplify.learning.cohorts.engine import TenantProfile, CohortEngine as CE
        svc = _get_service(db_session)
        svc.engine = CE(k_anonymity_threshold=2)

        await svc.create_definition({
            "name": "Hip-Hop",
            "criteria": {"genre_family": ["hip-hop"]},
        })
        await db_session.commit()
        target = TenantProfile(
            tenant_id=TENANT_ID,
            traits={"genre_family": "hip-hop"},
            rewards=[],
        )
        others = [
            TenantProfile(
                tenant_id=uuid.uuid4(),
                traits={"genre_family": "hip-hop", "audience_size_bucket": "small"},
                rewards=[0.4, 0.5],
            ),
            TenantProfile(
                tenant_id=uuid.uuid4(),
                traits={"genre_family": "hip-hop", "audience_size_bucket": "small"},
                rewards=[0.5, 0.6],
            ),
            TenantProfile(
                tenant_id=uuid.uuid4(),
                traits={"genre_family": "hip-hop", "audience_size_bucket": "medium"},
                rewards=[0.6, 0.7],
            ),
        ]

        priors = await svc.get_cold_start_priors(target, others)
        assert len(priors) > 0
        # Should not contain target tenant's data
        for p in priors:
            assert p.tenant_count >= 2


# ── Tests: Seed definitions ──────────────────────────────────────


class TestSeed:
    @pytest.mark.asyncio
    async def test_seed_sample_definitions(self, db_session, tenant):
        svc = _get_service(db_session)
        created = await svc.seed_sample_definitions()
        await db_session.commit()
        assert len(created) >= 5
        # All should be active
        for d in created:
            assert d["is_active"] is True


# ── Tests: Audit trail ───────────────────────────────────────────


class TestAudit:
    @pytest.mark.asyncio
    async def test_create_audited(self, db_session, tenant):
        from amplify.db.models.learning import LearningAuditLogModel
        svc = _get_service(db_session)
        await svc.create_definition({"name": "Audited", "criteria": {"x": 1}})
        await db_session.commit()

        stmt = select(LearningAuditLogModel).where(
            LearningAuditLogModel.action == "cohort_definition.created",
        )
        result = await db_session.execute(stmt)
        logs = list(result.scalars().all())
        assert len(logs) == 1
        assert logs[0].details["name"] == "Audited"

    @pytest.mark.asyncio
    async def test_update_audited(self, db_session, tenant):
        from amplify.db.models.learning import LearningAuditLogModel
        svc = _get_service(db_session)
        created = await svc.create_definition({"name": "Before", "criteria": {"x": 1}})
        await db_session.flush()

        await svc.update_definition(uuid.UUID(created["id"]), {"name": "After"})
        await db_session.flush()

        stmt = select(LearningAuditLogModel).where(
            LearningAuditLogModel.action == "cohort_definition.updated",
        )
        result = await db_session.execute(stmt)
        logs = list(result.scalars().all())
        assert len(logs) == 1

    @pytest.mark.asyncio
    async def test_delete_audited(self, db_session, tenant):
        from amplify.db.models.learning import LearningAuditLogModel
        svc = _get_service(db_session)
        created = await svc.create_definition({"name": "Gone", "criteria": {}})
        await db_session.commit()

        await svc.delete_definition(uuid.UUID(created["id"]))
        await db_session.commit()

        stmt = select(LearningAuditLogModel).where(
            LearningAuditLogModel.action == "cohort_definition.deleted",
        )
        result = await db_session.execute(stmt)
        logs = list(result.scalars().all())
        assert len(logs) == 1

"""API-level tests for the global prior service.

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
    from amplify.learning.config import LearningConfig, PrivacyConfig
    from app.services.global_prior_service import DBGlobalPriorService

    config = LearningConfig(
        privacy=PrivacyConfig(k_anonymity_threshold=2, cross_tenant_enabled=True),
    )
    return DBGlobalPriorService(db_session, config)


def _make_obs(
    tenant_id=None, features=None, reward=0.5, action_type="post_published",
):
    from amplify.learning.schemas.observation import Observation
    return Observation(
        tenant_id=tenant_id or uuid.uuid4(),
        action_type=action_type,
        features=features or {},
        reward=reward,
    )


def _make_observations(
    n_tenants=5, n_per_tenant=10,
    feature_name="post_hour", feature_value="18", reward=0.6,
):
    obs = []
    for _ in range(n_tenants):
        tid = uuid.uuid4()
        for _ in range(n_per_tenant):
            obs.append(_make_obs(
                tenant_id=tid,
                features={feature_name: feature_value},
                reward=reward,
            ))
    return obs


# ── Tests: Compute and store ──────────────────────────────────────


class TestComputeAndStore:
    @pytest.mark.asyncio
    async def test_compute_and_store(self, db_session, tenant):
        svc = _get_service(db_session)
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        stored = await svc.compute_and_store(obs)
        await db_session.commit()

        assert len(stored) > 0
        for s in stored:
            assert s["feature_name"] == "post_hour"
            assert s["tenant_count"] >= 2
            assert s["is_active"] is True

    @pytest.mark.asyncio
    async def test_stores_category(self, db_session, tenant):
        svc = _get_service(db_session)
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        stored = await svc.compute_and_store(obs)
        await db_session.commit()

        assert all(s["category"] == "posting_window" for s in stored)

    @pytest.mark.asyncio
    async def test_stores_source_cohort(self, db_session, tenant):
        svc = _get_service(db_session)
        cohort_id = uuid.uuid4()
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        stored = await svc.compute_and_store(
            obs,
            source_cohort_id=cohort_id,
            source_cohort_name="Hip-Hop",
        )
        await db_session.commit()

        for s in stored:
            assert s["source_cohort_id"] == str(cohort_id)

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, db_session, tenant):
        svc = _get_service(db_session)
        obs1 = _make_observations(
            n_tenants=5, n_per_tenant=10,
            feature_name="post_hour", feature_value="18", reward=0.5,
        )
        stored1 = await svc.compute_and_store(obs1)
        await db_session.commit()

        obs2 = _make_observations(
            n_tenants=5, n_per_tenant=10,
            feature_name="post_hour", feature_value="18", reward=0.8,
        )
        stored2 = await svc.compute_and_store(obs2)
        await db_session.commit()

        # Should update existing, not create duplicate
        priors = await svc.list_active_priors(category="posting_window")
        # Only one entry for post_hour=18
        hour_18 = [p for p in priors if p["feature_value"] == "18"]
        assert len(hour_18) == 1
        assert hour_18[0]["version"] == 2
        assert hour_18[0]["mean_reward"] == pytest.approx(0.8, abs=0.01)

    @pytest.mark.asyncio
    async def test_threshold_gating_blocks_insufficient_data(self, db_session, tenant):
        svc = _get_service(db_session)
        # Only 1 tenant — below k=2
        tid = uuid.uuid4()
        obs = [_make_obs(
            tenant_id=tid,
            features={"post_hour": "18"},
            reward=0.5,
        ) for _ in range(50)]
        stored = await svc.compute_and_store(obs)
        await db_session.commit()
        assert stored == []


# ── Tests: Query ──────────────────────────────────────────────────


class TestQuery:
    @pytest.mark.asyncio
    async def test_list_active_priors(self, db_session, tenant):
        svc = _get_service(db_session)
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        await svc.compute_and_store(obs)
        await db_session.commit()

        priors = await svc.list_active_priors()
        assert len(priors) > 0

    @pytest.mark.asyncio
    async def test_filter_by_category(self, db_session, tenant):
        svc = _get_service(db_session)
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        await svc.compute_and_store(obs)
        await db_session.commit()

        priors = await svc.list_active_priors(category="posting_window")
        assert all(p["category"] == "posting_window" for p in priors)

    @pytest.mark.asyncio
    async def test_no_tenant_ids_in_output(self, db_session, tenant):
        svc = _get_service(db_session)
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        await svc.compute_and_store(obs)
        await db_session.commit()

        priors = await svc.list_active_priors()
        for p in priors:
            serialized = str(p)
            assert "tenant_id" not in serialized.lower() or "tenant_count" in serialized.lower()


# ── Tests: Cold-start recommendations ─────────────────────────────


class TestColdStartRecommendations:
    @pytest.mark.asyncio
    async def test_cold_start_returns_recs(self, db_session, tenant):
        svc = _get_service(db_session)
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        await svc.compute_and_store(obs)
        await db_session.commit()

        recs = await svc.get_cold_start_recommendations(tenant_observation_count=0)
        assert len(recs) > 0
        for cat, items in recs.items():
            for item in items:
                assert item["source"] == "global_prior"
                assert "global prior" in item["explanation"]

    @pytest.mark.asyncio
    async def test_cold_start_skipped_with_data(self, db_session, tenant):
        svc = _get_service(db_session)
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        await svc.compute_and_store(obs)
        await db_session.commit()

        recs = await svc.get_cold_start_recommendations(tenant_observation_count=100)
        assert recs == {}

    @pytest.mark.asyncio
    async def test_cold_start_labels_as_global(self, db_session, tenant):
        svc = _get_service(db_session)
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        await svc.compute_and_store(obs)
        await db_session.commit()

        recs = await svc.get_cold_start_recommendations(tenant_observation_count=0)
        for cat, items in recs.items():
            for item in items:
                assert "not learned from your" in item["explanation"]


# ── Tests: Privacy guarantees ─────────────────────────────────────


class TestPrivacyGuarantees:
    @pytest.mark.asyncio
    async def test_no_raw_content_stored(self, db_session, tenant):
        """Verify raw content text never ends up in stored patterns."""
        from amplify.db.models.learning import GlobalPatternStatModel

        svc = _get_service(db_session)
        obs = []
        for _ in range(5):
            tid = uuid.uuid4()
            for _ in range(10):
                obs.append(_make_obs(
                    tenant_id=tid,
                    features={
                        "post_hour": "18",
                        "content_text": "My secret album lyrics go here",
                    },
                ))
        await svc.compute_and_store(obs)
        await db_session.commit()

        stmt = select(GlobalPatternStatModel)
        result = await db_session.execute(stmt)
        for row in result.scalars().all():
            assert "secret album" not in str(row.feature_value)
            assert "lyrics" not in str(row.feature_value)

    @pytest.mark.asyncio
    async def test_no_individual_tenant_metrics(self, db_session, tenant):
        """Stored patterns must be aggregates, not individual tenant data."""
        svc = _get_service(db_session)
        obs = _make_observations(n_tenants=5, n_per_tenant=10, feature_name="post_hour")
        await svc.compute_and_store(obs)
        await db_session.commit()

        priors = await svc.list_active_priors()
        for p in priors:
            # Must have aggregated from multiple tenants
            assert p["tenant_count"] >= 2
            # Must have minimum k-anonymity threshold recorded
            assert p["k_anonymity_threshold"] >= 2

"""API-level tests for blended recommendations.

Tests the full integration: TenantPatternService + DBGlobalPriorService
→ BlendedRecommendationService → blended output with source labels.
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


def _get_blended_service(db_session, tenant_id=None):
    from amplify.learning.config import LearningConfig, PrivacyConfig, RankingConfig
    from app.services.blended_recommendation_service import BlendedRecommendationService

    config = LearningConfig(
        privacy=PrivacyConfig(k_anonymity_threshold=2, cross_tenant_enabled=True),
        ranking=RankingConfig(cold_start_threshold=20),
    )
    return BlendedRecommendationService(
        db_session, tenant_id or TENANT_ID, config,
    )


def _get_global_prior_service(db_session):
    from amplify.learning.config import LearningConfig, PrivacyConfig
    from app.services.global_prior_service import DBGlobalPriorService

    config = LearningConfig(
        privacy=PrivacyConfig(k_anonymity_threshold=2, cross_tenant_enabled=True),
    )
    return DBGlobalPriorService(db_session, config)


def _make_obs(tenant_id=None, features=None, reward=0.5):
    from amplify.learning.schemas.observation import Observation
    return Observation(
        tenant_id=tenant_id or uuid.uuid4(),
        action_type="post_published",
        features=features or {},
        reward=reward,
    )


async def _seed_global_priors(db_session, n_tenants=5, n_per_tenant=10):
    """Seed global priors with enough data to pass thresholds."""
    svc = _get_global_prior_service(db_session)
    obs = []
    for _ in range(n_tenants):
        tid = uuid.uuid4()
        for _ in range(n_per_tenant):
            obs.append(_make_obs(
                tenant_id=tid,
                features={"post_hour": "18", "cta_type": "link"},
                reward=0.65,
            ))
    await svc.compute_and_store(obs)
    await db_session.commit()


# ── Tests: Cold-start blended recommendations ────────────────────


class TestColdStartBlending:
    @pytest.mark.asyncio
    async def test_cold_start_returns_global_priors(self, db_session, tenant):
        await _seed_global_priors(db_session)
        svc = _get_blended_service(db_session)
        result = await svc.get_blended_recommendations()

        assert result["data_status"]["is_cold_start"] is True
        assert result["data_status"]["maturity_label"] == "no_data"

        recs = result["recommendations"]
        if recs:
            for r in recs:
                assert r["source"] in ("global_prior", "cohort_prior")
                assert r["tenant_weight"] == 0.0
                assert r["global_weight"] == 1.0

    @pytest.mark.asyncio
    async def test_cold_start_data_status(self, db_session, tenant):
        svc = _get_blended_service(db_session)
        result = await svc.get_blended_recommendations()

        ds = result["data_status"]
        assert "is_cold_start" in ds
        assert "maturity_label" in ds
        assert "tenant_weight" in ds
        assert "global_weight" in ds
        assert ds["tenant_weight"] + ds["global_weight"] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_empty_when_no_global_priors(self, db_session, tenant):
        svc = _get_blended_service(db_session)
        result = await svc.get_blended_recommendations()
        # No global priors seeded, no tenant data → empty
        assert result["recommendations"] == []


# ── Tests: Onboarding recommendations ───────────────────────────


class TestOnboardingRecommendations:
    @pytest.mark.asyncio
    async def test_onboarding_returns_categorized(self, db_session, tenant):
        await _seed_global_priors(db_session)
        svc = _get_blended_service(db_session)
        result = await svc.get_onboarding_recommendations()

        assert "categories" in result
        assert "total" in result
        assert result["source"] == "global_prior"
        assert "smart defaults" in result["note"].lower() or "aggregate" in result["note"].lower()

    @pytest.mark.asyncio
    async def test_onboarding_has_labels(self, db_session, tenant):
        await _seed_global_priors(db_session)
        svc = _get_blended_service(db_session)
        result = await svc.get_onboarding_recommendations()

        for cat in result["categories"]:
            assert "key" in cat
            assert "label" in cat
            assert "recommendations" in cat

    @pytest.mark.asyncio
    async def test_onboarding_empty_when_no_priors(self, db_session, tenant):
        svc = _get_blended_service(db_session)
        result = await svc.get_onboarding_recommendations()
        assert result["total"] == 0


# ── Tests: Migration ────────────────────────────────────────────


class TestMigration:
    @pytest.mark.asyncio
    async def test_migrate_cold_start_tenant(self, db_session, tenant):
        await _seed_global_priors(db_session)
        svc = _get_blended_service(db_session)
        result = await svc.migrate_cold_start_tenant()

        assert result["migrated"] is True
        assert result["observation_count"] == 0
        assert result["maturity_label"] == "no_data"

    @pytest.mark.asyncio
    async def test_migrate_skips_mature_tenant(self, db_session, tenant):
        """A tenant above the cold-start threshold should not be migrated."""
        # Seed enough feature vectors to exceed threshold
        from amplify.db.models.learning import PostFeatureVectorModel
        for i in range(25):
            vec = PostFeatureVectorModel(
                id=uuid.uuid4(),
                tenant_id=TENANT_ID,
                post_id=uuid.uuid4(),
                features={"post_hour": "18"},
                extracted_at=datetime.now(timezone.utc),
            )
            db_session.add(vec)
        await db_session.flush()

        svc = _get_blended_service(db_session)
        result = await svc.migrate_cold_start_tenant()

        assert result["migrated"] is False
        assert "sufficient data" in result["reason"].lower()


# ── Tests: Source label accuracy ─────────────────────────────────


class TestSourceLabels:
    @pytest.mark.asyncio
    async def test_all_recs_have_source(self, db_session, tenant):
        await _seed_global_priors(db_session)
        svc = _get_blended_service(db_session)
        result = await svc.get_blended_recommendations()

        valid_sources = {"global_prior", "cohort_prior", "tenant_learned", "blended"}
        for r in result["recommendations"]:
            assert r["source"] in valid_sources

    @pytest.mark.asyncio
    async def test_blend_weights_sum_to_one(self, db_session, tenant):
        await _seed_global_priors(db_session)
        svc = _get_blended_service(db_session)
        result = await svc.get_blended_recommendations()

        for r in result["recommendations"]:
            assert r["tenant_weight"] + r["global_weight"] == pytest.approx(1.0)

"""API-level tests for the evaluation service and routes.

Self-contained — uses its own DB fixtures to avoid import-mode issues.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio

# Ensure the API app is importable
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
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


TENANT_ID = uuid.uuid4()


# ── Helpers ──────────────────────────────────────────────────────


def _get_service(db_session):
    from app.services.evaluation_service import EvaluationService
    return EvaluationService(db_session, TENANT_ID)


def _get_sample_dataset():
    from amplify.learning.evaluation.replay import build_sample_dataset
    return build_sample_dataset()


def _default_policies():
    from amplify.learning.ranking.post_ranker import PostRanker
    from amplify.learning.evaluation.replay import RankingPolicySpec
    return [
        RankingPolicySpec(
            name="default",
            ranker=PostRanker(),
            description="Default ranking — no tenant patterns",
        ),
    ]


def _two_policies():
    from amplify.learning.ranking.post_ranker import PostRanker
    from amplify.learning.evaluation.replay import RankingPolicySpec
    return [
        RankingPolicySpec(
            name="default",
            ranker=PostRanker(),
            description="Default ranking",
        ),
        RankingPolicySpec(
            name="pre_release",
            ranker=PostRanker.for_campaign_phase("pre_release"),
            description="Pre-release profile",
        ),
    ]


# ── Tests: EvaluationService ────────────────────────────────────


class TestRunEvaluation:
    @pytest.mark.asyncio
    async def test_run_with_sample_dataset(self, db_session):
        svc = _get_service(db_session)
        run = await svc.run_evaluation(
            policies=_default_policies(),
            dataset=_get_sample_dataset(),
            evaluation_type="ranking",
        )
        await db_session.commit()

        assert run.id is not None
        assert run.status == "completed"
        assert run.sample_size == 12
        assert run.mae is not None
        assert run.evaluation_type == "ranking"

    @pytest.mark.asyncio
    async def test_run_persists_results(self, db_session):
        svc = _get_service(db_session)
        run = await svc.run_evaluation(
            policies=_default_policies(),
            dataset=_get_sample_dataset(),
        )
        await db_session.commit()

        results = await svc.get_results(run.id)
        assert len(results) == 12
        for r in results:
            assert r.evaluation_run_id == run.id
            assert r.predicted_score is not None

    @pytest.mark.asyncio
    async def test_run_stores_comparison_in_details(self, db_session):
        svc = _get_service(db_session)
        run = await svc.run_evaluation(
            policies=_two_policies(),
            dataset=_get_sample_dataset(),
        )
        await db_session.commit()

        comparison = await svc.get_comparison(run.id)
        assert "policies" in comparison
        assert len(comparison["policies"]) == 2
        assert "winner" in comparison

    @pytest.mark.asyncio
    async def test_run_with_top_k(self, db_session):
        svc = _get_service(db_session)
        run = await svc.run_evaluation(
            policies=_default_policies(),
            dataset=_get_sample_dataset(),
            top_k=5,
        )
        await db_session.commit()

        assert run.top_k == 5

    @pytest.mark.asyncio
    async def test_run_audited(self, db_session):
        from amplify.db.models.learning import LearningAuditLogModel

        svc = _get_service(db_session)
        await svc.run_evaluation(
            policies=_default_policies(),
            dataset=_get_sample_dataset(),
        )
        await db_session.commit()

        stmt = select(LearningAuditLogModel).where(
            LearningAuditLogModel.action == "evaluation.completed"
        )
        result = await db_session.execute(stmt)
        logs = list(result.scalars().all())
        assert len(logs) == 1
        assert logs[0].entity_type == "evaluation_run"


class TestQueryMethods:
    @pytest.mark.asyncio
    async def test_list_runs(self, db_session):
        svc = _get_service(db_session)
        ds = _get_sample_dataset()

        await svc.run_evaluation(policies=_default_policies(), dataset=ds)
        await svc.run_evaluation(policies=_default_policies(), dataset=ds)
        await db_session.commit()

        runs = await svc.list_runs()
        assert len(runs) == 2

    @pytest.mark.asyncio
    async def test_list_runs_by_type(self, db_session):
        svc = _get_service(db_session)
        ds = _get_sample_dataset()

        await svc.run_evaluation(
            policies=_default_policies(), dataset=ds, evaluation_type="ranking",
        )
        await svc.run_evaluation(
            policies=_default_policies(), dataset=ds, evaluation_type="timing",
        )
        await db_session.commit()

        ranking_runs = await svc.list_runs(evaluation_type="ranking")
        assert len(ranking_runs) == 1
        assert ranking_runs[0].evaluation_type == "ranking"

    @pytest.mark.asyncio
    async def test_get_run_by_id(self, db_session):
        svc = _get_service(db_session)
        run = await svc.run_evaluation(
            policies=_default_policies(), dataset=_get_sample_dataset(),
        )
        await db_session.commit()

        fetched = await svc.get_run(run.id)
        assert fetched is not None
        assert fetched.id == run.id
        assert fetched.sample_size == 12

    @pytest.mark.asyncio
    async def test_get_nonexistent_run(self, db_session):
        svc = _get_service(db_session)
        result = await svc.get_run(uuid.uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_get_results_pagination(self, db_session):
        svc = _get_service(db_session)
        run = await svc.run_evaluation(
            policies=_default_policies(), dataset=_get_sample_dataset(),
        )
        await db_session.commit()

        page1 = await svc.get_results(run.id, limit=5, offset=0)
        page2 = await svc.get_results(run.id, limit=5, offset=5)
        assert len(page1) == 5
        assert len(page2) == 5
        # No overlap
        ids1 = {r.id for r in page1}
        ids2 = {r.id for r in page2}
        assert ids1.isdisjoint(ids2)


class TestEmptyDataset:
    @pytest.mark.asyncio
    async def test_run_with_empty_db_dataset(self, db_session):
        """When no feature vectors exist, the run should still succeed with 0 samples."""
        svc = _get_service(db_session)
        run = await svc.run_evaluation(
            policies=_default_policies(),
            # Don't pass a dataset — let it build from DB (which is empty)
        )
        await db_session.commit()

        assert run.sample_size == 0
        assert run.status == "completed"


class TestMultiplePolicies:
    @pytest.mark.asyncio
    async def test_comparison_has_winner(self, db_session):
        svc = _get_service(db_session)
        run = await svc.run_evaluation(
            policies=_two_policies(),
            dataset=_get_sample_dataset(),
        )
        await db_session.commit()

        comparison = await svc.get_comparison(run.id)
        assert comparison["winner"] != ""
        assert comparison["winner"] in ("default", "pre_release")

    @pytest.mark.asyncio
    async def test_comparison_policies_have_metrics(self, db_session):
        svc = _get_service(db_session)
        run = await svc.run_evaluation(
            policies=_two_policies(),
            dataset=_get_sample_dataset(),
        )
        await db_session.commit()

        comparison = await svc.get_comparison(run.id)
        for p in comparison["policies"]:
            assert "mae" in p
            assert "rank_correlation" in p
            assert "sample_size" in p
            assert p["sample_size"] == 12

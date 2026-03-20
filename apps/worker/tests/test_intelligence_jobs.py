"""Tests for intelligence pipeline scheduled jobs."""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager

import pytest


# ── Helpers ──────────────────────────────────────────────────────


def _fake_session_factory(mock_db):
    @asynccontextmanager
    async def fake_session(url):
        yield mock_db
    return fake_session


def _mock_db_with_results(rows):
    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = rows
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.scalar = AsyncMock(return_value=0)
    return mock_db


def _ensure_module(dotted_path):
    """Ensure a dotted module path exists in sys.modules, creating MagicMocks as needed."""
    parts = dotted_path.split(".")
    for i in range(len(parts)):
        mod_path = ".".join(parts[: i + 1])
        if mod_path not in sys.modules:
            sys.modules[mod_path] = MagicMock()


# ── Feature Extraction ───────────────────────────────────────────


class TestExtractFeatures:
    @pytest.mark.asyncio
    async def test_no_posts_returns_zero(self):
        mock_db = _mock_db_with_results([])
        with patch("app.jobs.intelligence_jobs.get_async_session", _fake_session_factory(mock_db)):
            from app.jobs.intelligence_jobs import extract_features
            result = await extract_features({})
        assert result["extracted"] == 0
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_extracts_features_from_posts(self):
        post = MagicMock()
        post.id = uuid.uuid4()
        post.tenant_id = uuid.uuid4()

        mock_db = _mock_db_with_results([post])

        mock_extractor = MagicMock()
        mock_extractor.extract.return_value = {
            "content_type": "image",
            "post_hour": 14,
            "post_day_of_week": 2,
            "caption_length": 50,
            "hashtag_count": 3,
            "has_link": False,
            "campaign_phase": "launch",
        }
        _ensure_module("amplify.learning.feature_extraction.extractor")
        sys.modules["amplify.learning.feature_extraction.extractor"].FeatureExtractor = MagicMock(
            return_value=mock_extractor
        )

        with patch("app.jobs.intelligence_jobs.get_async_session", _fake_session_factory(mock_db)):
            from app.jobs.intelligence_jobs import extract_features
            result = await extract_features({})

        assert result["extracted"] == 1
        assert result["errors"] == 0
        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_extraction_error_gracefully(self):
        post = MagicMock()
        post.id = uuid.uuid4()
        post.tenant_id = uuid.uuid4()

        mock_db = _mock_db_with_results([post])

        _ensure_module("amplify.learning.feature_extraction.extractor")
        sys.modules["amplify.learning.feature_extraction.extractor"].FeatureExtractor = MagicMock(
            side_effect=Exception("bad feature")
        )

        with patch("app.jobs.intelligence_jobs.get_async_session", _fake_session_factory(mock_db)):
            from app.jobs.intelligence_jobs import extract_features
            result = await extract_features({})

        assert result["extracted"] == 0
        assert result["errors"] == 1

    @pytest.mark.asyncio
    async def test_tenant_filter(self):
        mock_db = _mock_db_with_results([])
        tid = str(uuid.uuid4())
        with patch("app.jobs.intelligence_jobs.get_async_session", _fake_session_factory(mock_db)):
            from app.jobs.intelligence_jobs import extract_features
            result = await extract_features({"tenant_id": tid})
        assert result["extracted"] == 0


# ── Reward Computation ───────────────────────────────────────────


class TestComputeRewards:
    @pytest.mark.asyncio
    async def test_no_pending_outcomes(self):
        mock_db = _mock_db_with_results([])
        with patch("app.jobs.intelligence_jobs.get_async_session", _fake_session_factory(mock_db)):
            from app.jobs.intelligence_jobs import compute_rewards
            result = await compute_rewards({})
        assert result["computed"] == 0
        assert result["total_pending"] == 0

    @pytest.mark.asyncio
    async def test_computes_rewards(self):
        outcome = MagicMock()
        outcome.id = uuid.uuid4()
        outcome.reward = None

        mock_db = _mock_db_with_results([outcome])

        mock_calc = MagicMock()
        mock_calc.compute.return_value = (0.75, {"engagement": 0.5, "reach": 0.25})
        _ensure_module("amplify.learning.rewards.calculator")
        sys.modules["amplify.learning.rewards.calculator"].RewardCalculator = MagicMock(
            return_value=mock_calc
        )

        with patch("app.jobs.intelligence_jobs.get_async_session", _fake_session_factory(mock_db)):
            from app.jobs.intelligence_jobs import compute_rewards
            result = await compute_rewards({})

        assert result["computed"] == 1
        assert outcome.reward == 0.75
        mock_db.commit.assert_awaited_once()


# ── Tenant Pattern Update ────────────────────────────────────────


class TestUpdateTenantPatterns:
    @pytest.mark.asyncio
    async def test_specific_tenant(self):
        mock_db = _mock_db_with_results([])
        mock_db.commit = AsyncMock()

        mock_svc = AsyncMock()
        mock_svc.update_patterns = AsyncMock(return_value=[MagicMock(), MagicMock()])

        _ensure_module("app.services.tenant_pattern_service")
        sys.modules["app.services.tenant_pattern_service"].TenantPatternService = MagicMock(
            return_value=mock_svc
        )

        tid = str(uuid.uuid4())
        with patch("app.jobs.intelligence_jobs.get_async_session", _fake_session_factory(mock_db)):
            from app.jobs.intelligence_jobs import update_tenant_patterns
            result = await update_tenant_patterns({"tenant_id": tid})

        assert result["tenants_processed"] == 1
        assert result["patterns_updated"] == 2
        assert result["errors"] == 0

    @pytest.mark.asyncio
    async def test_handles_tenant_error_with_rollback(self):
        mock_db = _mock_db_with_results([])
        mock_db.commit = AsyncMock()
        mock_db.rollback = AsyncMock()

        mock_svc = AsyncMock()
        mock_svc.update_patterns = AsyncMock(side_effect=Exception("db error"))

        _ensure_module("app.services.tenant_pattern_service")
        sys.modules["app.services.tenant_pattern_service"].TenantPatternService = MagicMock(
            return_value=mock_svc
        )

        tid = str(uuid.uuid4())
        with patch("app.jobs.intelligence_jobs.get_async_session", _fake_session_factory(mock_db)):
            from app.jobs.intelligence_jobs import update_tenant_patterns
            result = await update_tenant_patterns({"tenant_id": tid})

        assert result["errors"] == 1
        mock_db.rollback.assert_awaited_once()


# ── Cohort Aggregation ───────────────────────────────────────────


class TestAggregateCohorts:
    @pytest.mark.asyncio
    async def test_empty_observations(self):
        mock_db = _mock_db_with_results([])
        mock_db.commit = AsyncMock()

        mock_svc = AsyncMock()
        mock_svc.compute_and_store = AsyncMock(return_value=[])

        _ensure_module("app.services.global_prior_service")
        sys.modules["app.services.global_prior_service"].DBGlobalPriorService = MagicMock(
            return_value=mock_svc
        )
        _ensure_module("amplify.learning.schemas.observation")
        mock_obs_cls = MagicMock()
        sys.modules["amplify.learning.schemas.observation"].Observation = mock_obs_cls

        with patch("app.jobs.intelligence_jobs.get_async_session", _fake_session_factory(mock_db)):
            from app.jobs.intelligence_jobs import aggregate_cohorts
            result = await aggregate_cohorts({})

        assert result["observations"] == 0
        assert result["priors_stored"] == 0

    @pytest.mark.asyncio
    async def test_aggregates_observations(self):
        event = MagicMock()
        event.tenant_id = uuid.uuid4()
        event.action_type = "post"
        event.features = {"content_type": "video"}
        event.reward = 0.8

        mock_db = _mock_db_with_results([event])
        mock_db.commit = AsyncMock()

        stored_prior = MagicMock()
        mock_svc = AsyncMock()
        mock_svc.compute_and_store = AsyncMock(return_value=[stored_prior])

        _ensure_module("app.services.global_prior_service")
        sys.modules["app.services.global_prior_service"].DBGlobalPriorService = MagicMock(
            return_value=mock_svc
        )
        _ensure_module("amplify.learning.schemas.observation")
        mock_obs_cls = MagicMock()
        sys.modules["amplify.learning.schemas.observation"].Observation = mock_obs_cls

        with patch("app.jobs.intelligence_jobs.get_async_session", _fake_session_factory(mock_db)):
            from app.jobs.intelligence_jobs import aggregate_cohorts
            result = await aggregate_cohorts({"days_back": 7})

        assert result["observations"] == 1
        assert result["priors_stored"] == 1


# ── Evaluation Run ───────────────────────────────────────────────


class TestRunEvaluation:
    @pytest.mark.asyncio
    async def test_requires_tenant_id(self):
        from app.jobs.intelligence_jobs import run_evaluation
        result = await run_evaluation({})
        assert result["status"] == "error"
        assert "tenant_id" in result["error"]

    @pytest.mark.asyncio
    async def test_runs_evaluation(self):
        mock_run = MagicMock()
        mock_run.id = uuid.uuid4()
        mock_run.mae = 0.15
        mock_run.rank_correlation = 0.72
        mock_run.sample_size = 50

        mock_db = _mock_db_with_results([])
        mock_db.commit = AsyncMock()

        mock_svc = AsyncMock()
        mock_svc.run_evaluation = AsyncMock(return_value=mock_run)

        _ensure_module("app.services.evaluation_service")
        sys.modules["app.services.evaluation_service"].EvaluationService = MagicMock(
            return_value=mock_svc
        )
        _ensure_module("amplify.learning.ranking.post_ranker")
        sys.modules["amplify.learning.ranking.post_ranker"].PostRanker = MagicMock
        _ensure_module("amplify.learning.evaluation.replay")
        sys.modules["amplify.learning.evaluation.replay"].RankingPolicySpec = MagicMock

        tid = str(uuid.uuid4())
        with patch("app.jobs.intelligence_jobs.get_async_session", _fake_session_factory(mock_db)):
            from app.jobs.intelligence_jobs import run_evaluation
            result = await run_evaluation({"tenant_id": tid})

        assert result["mae"] == 0.15
        assert result["rank_correlation"] == 0.72
        assert result["sample_size"] == 50


# ── Model Health Check ───────────────────────────────────────────


class TestCheckModelHealth:
    @pytest.mark.asyncio
    async def test_healthy_when_no_alerts(self):
        mock_db = AsyncMock()
        mock_db.scalar = AsyncMock(return_value=0)
        mock_eval_result = MagicMock()
        mock_eval_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_eval_result)

        with patch("app.jobs.intelligence_jobs.get_async_session", _fake_session_factory(mock_db)):
            from app.jobs.intelligence_jobs import check_model_health
            result = await check_model_health({})

        assert result["status"] == "healthy"
        assert len(result["alerts"]) == 0

    @pytest.mark.asyncio
    async def test_degraded_with_stale_features(self):
        call_count = 0

        async def mock_scalar(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return 15
            return 0

        mock_db = AsyncMock()
        mock_db.scalar = mock_scalar
        mock_eval_result = MagicMock()
        mock_eval_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_eval_result)

        with patch("app.jobs.intelligence_jobs.get_async_session", _fake_session_factory(mock_db)):
            from app.jobs.intelligence_jobs import check_model_health
            result = await check_model_health({})

        assert result["status"] == "degraded"
        assert any(a["type"] == "stale_features" for a in result["alerts"])

    @pytest.mark.asyncio
    async def test_evaluation_regression_detected(self):
        mock_db = AsyncMock()
        mock_db.scalar = AsyncMock(return_value=0)

        run_current = MagicMock()
        run_current.mae = 0.50
        run_current.created_at = datetime.now(timezone.utc)
        run_previous = MagicMock()
        run_previous.mae = 0.20
        run_previous.created_at = datetime.now(timezone.utc) - timedelta(days=1)

        mock_eval_result = MagicMock()
        mock_eval_result.scalars.return_value.all.return_value = [run_current, run_previous]
        mock_db.execute = AsyncMock(return_value=mock_eval_result)

        with patch("app.jobs.intelligence_jobs.get_async_session", _fake_session_factory(mock_db)):
            from app.jobs.intelligence_jobs import check_model_health
            result = await check_model_health({})

        assert result["status"] == "degraded"
        regression_alerts = [a for a in result["alerts"] if a["type"] == "evaluation_regression"]
        assert len(regression_alerts) == 1
        assert regression_alerts[0]["current_mae"] == 0.50

    @pytest.mark.asyncio
    async def test_missing_rewards_alert(self):
        call_count = 0

        async def mock_scalar(stmt):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return 25
            return 0

        mock_db = AsyncMock()
        mock_db.scalar = mock_scalar
        mock_eval_result = MagicMock()
        mock_eval_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_eval_result)

        with patch("app.jobs.intelligence_jobs.get_async_session", _fake_session_factory(mock_db)):
            from app.jobs.intelligence_jobs import check_model_health
            result = await check_model_health({})

        assert result["status"] == "degraded"
        assert any(a["type"] == "missing_rewards" for a in result["alerts"])


# ── Scheduler Registration ───────────────────────────────────────


class TestSchedulerWiring:
    def test_all_intelligence_jobs_importable(self):
        """Verify all 6 intelligence jobs can be imported from the jobs module."""
        from app.jobs.intelligence_jobs import (
            extract_features,
            compute_rewards,
            update_tenant_patterns,
            aggregate_cohorts,
            run_evaluation,
            check_model_health,
        )
        assert callable(extract_features)
        assert callable(compute_rewards)
        assert callable(update_tenant_patterns)
        assert callable(aggregate_cohorts)
        assert callable(run_evaluation)
        assert callable(check_model_health)

    def test_all_jobs_are_async(self):
        """All intelligence jobs must be async functions."""
        import asyncio
        from app.jobs.intelligence_jobs import (
            extract_features,
            compute_rewards,
            update_tenant_patterns,
            aggregate_cohorts,
            run_evaluation,
            check_model_health,
        )
        for fn in [extract_features, compute_rewards, update_tenant_patterns,
                    aggregate_cohorts, run_evaluation, check_model_health]:
            assert asyncio.iscoroutinefunction(fn), f"{fn.__name__} is not async"

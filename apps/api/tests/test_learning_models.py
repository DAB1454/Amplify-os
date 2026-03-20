"""Tests for learning subsystem ORM models and relationships.

Verifies that all 11 learning models can be created, persisted, and
queried through SQLAlchemy async sessions using the SQLite test backend.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from amplify.db.models.learning import (
    CohortDefinitionModel,
    EvaluationResultModel,
    EvaluationRunModel,
    GlobalPatternStatModel,
    LearningAuditLogModel,
    LearningEventModel,
    PostFeatureVectorModel,
    PostOutcomeModel,
    PromptAssignmentModel,
    PromptVersionModel,
    TenantPatternModel,
)

# Fixed test IDs matching TenantMiddleware local mode (same as conftest.py)
TEST_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


# ── Helpers ──────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── LearningEvent ───────────────────────────────────────────────


class TestLearningEventModel:
    @pytest.mark.asyncio
    async def test_create_minimal(self, db_session: AsyncSession, seed_tenant):
        event = LearningEventModel(
            tenant_id=TEST_TENANT_ID,
            action_type="post_published",
            observed_at=_now(),
        )
        db_session.add(event)
        await db_session.commit()

        result = await db_session.execute(
            select(LearningEventModel).where(LearningEventModel.id == event.id)
        )
        row = result.scalar_one()
        assert row.action_type == "post_published"
        assert row.tenant_id == TEST_TENANT_ID
        assert row.features == {}
        assert row.outcomes == {}
        assert row.confidence == 1.0

    @pytest.mark.asyncio
    async def test_create_with_all_fks(self, db_session: AsyncSession, seed_artist, seed_channel):
        from amplify.db.models.campaign import CampaignModel
        from amplify.db.models.post import PostModel

        campaign = CampaignModel(
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            artist_id=seed_artist.id,
            name="Test Campaign",
        )
        db_session.add(campaign)
        await db_session.flush()

        post = PostModel(
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            channel_id=seed_channel.id,
            platform="instagram",
        )
        db_session.add(post)
        await db_session.flush()

        event = LearningEventModel(
            tenant_id=TEST_TENANT_ID,
            action_type="post_published",
            artist_id=seed_artist.id,
            campaign_id=campaign.id,
            post_id=post.id,
            platform="instagram",
            features={"post_hour": 18, "content_type": "carousel"},
            outcomes={"engagement_rate": 0.045},
            reward=0.72,
            reward_breakdown={"engagement": 0.5, "reach": 0.22},
            observed_at=_now(),
            source="worker",
        )
        db_session.add(event)
        await db_session.commit()

        result = await db_session.execute(
            select(LearningEventModel).where(LearningEventModel.id == event.id)
        )
        row = result.scalar_one()
        assert row.artist_id == seed_artist.id
        assert row.campaign_id == campaign.id
        assert row.post_id == post.id
        assert row.reward == pytest.approx(0.72)
        assert row.features["post_hour"] == 18

    @pytest.mark.asyncio
    async def test_schema_version_defaults_to_1(self, db_session: AsyncSession, seed_tenant):
        event = LearningEventModel(
            tenant_id=TEST_TENANT_ID,
            action_type="test",
            observed_at=_now(),
        )
        db_session.add(event)
        await db_session.commit()
        assert event.schema_version == 1


# ── PostFeatureVector ───────────────────────────────────────────


class TestPostFeatureVectorModel:
    @pytest.mark.asyncio
    async def test_create_with_typed_features(self, db_session: AsyncSession, seed_channel):
        from amplify.db.models.post import PostModel

        post = PostModel(
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            channel_id=seed_channel.id,
            platform="instagram",
        )
        db_session.add(post)
        await db_session.flush()

        fv = PostFeatureVectorModel(
            tenant_id=TEST_TENANT_ID,
            post_id=post.id,
            features={"post_hour": 14, "content_type": "reel"},
            platform="instagram",
            content_type="reel",
            post_hour=14,
            post_day_of_week=2,
            caption_length=120,
            hashtag_count=5,
            has_link=False,
            campaign_phase="release_day",
            extracted_at=_now(),
        )
        db_session.add(fv)
        await db_session.commit()

        result = await db_session.execute(
            select(PostFeatureVectorModel).where(PostFeatureVectorModel.id == fv.id)
        )
        row = result.scalar_one()
        assert row.post_hour == 14
        assert row.content_type == "reel"
        assert row.has_link is False
        assert row.features["content_type"] == "reel"

    @pytest.mark.asyncio
    async def test_unique_per_post(self, db_session: AsyncSession, seed_channel):
        from sqlalchemy.exc import IntegrityError
        from amplify.db.models.post import PostModel

        post = PostModel(
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            channel_id=seed_channel.id,
            platform="tiktok",
        )
        db_session.add(post)
        await db_session.flush()

        fv1 = PostFeatureVectorModel(
            tenant_id=TEST_TENANT_ID,
            post_id=post.id,
            features={"v": 1},
            extracted_at=_now(),
        )
        db_session.add(fv1)
        await db_session.flush()

        fv2 = PostFeatureVectorModel(
            tenant_id=TEST_TENANT_ID,
            post_id=post.id,
            features={"v": 2},
            extracted_at=_now(),
        )
        db_session.add(fv2)
        with pytest.raises(IntegrityError):
            await db_session.flush()


# ── PostOutcome ─────────────────────────────────────────────────


class TestPostOutcomeModel:
    @pytest.mark.asyncio
    async def test_create_with_metrics(self, db_session: AsyncSession, seed_channel):
        from amplify.db.models.post import PostModel

        post = PostModel(
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            channel_id=seed_channel.id,
            platform="instagram",
        )
        db_session.add(post)
        await db_session.flush()

        outcome = PostOutcomeModel(
            tenant_id=TEST_TENANT_ID,
            post_id=post.id,
            measurement_window="24h",
            impressions=5000,
            reach=3200,
            engagements=225,
            saves=45,
            shares=12,
            engagement_rate=0.045,
            save_rate=0.009,
            reward=0.68,
            measured_at=_now(),
        )
        db_session.add(outcome)
        await db_session.commit()

        result = await db_session.execute(
            select(PostOutcomeModel).where(PostOutcomeModel.id == outcome.id)
        )
        row = result.scalar_one()
        assert row.impressions == 5000
        assert row.engagement_rate == pytest.approx(0.045)
        assert row.measurement_window == "24h"

    @pytest.mark.asyncio
    async def test_unique_post_window(self, db_session: AsyncSession, seed_channel):
        from sqlalchemy.exc import IntegrityError
        from amplify.db.models.post import PostModel

        post = PostModel(
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            channel_id=seed_channel.id,
            platform="instagram",
        )
        db_session.add(post)
        await db_session.flush()

        o1 = PostOutcomeModel(
            tenant_id=TEST_TENANT_ID,
            post_id=post.id,
            measurement_window="24h",
            measured_at=_now(),
        )
        db_session.add(o1)
        await db_session.flush()

        o2 = PostOutcomeModel(
            tenant_id=TEST_TENANT_ID,
            post_id=post.id,
            measurement_window="24h",
            measured_at=_now(),
        )
        db_session.add(o2)
        with pytest.raises(IntegrityError):
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_multiple_windows_same_post(self, db_session: AsyncSession, seed_channel):
        from amplify.db.models.post import PostModel

        post = PostModel(
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            channel_id=seed_channel.id,
            platform="instagram",
        )
        db_session.add(post)
        await db_session.flush()

        for window in ("24h", "48h", "7d"):
            db_session.add(PostOutcomeModel(
                tenant_id=TEST_TENANT_ID,
                post_id=post.id,
                measurement_window=window,
                measured_at=_now(),
            ))
        await db_session.commit()

        result = await db_session.execute(
            select(PostOutcomeModel).where(PostOutcomeModel.post_id == post.id)
        )
        assert len(result.scalars().all()) == 3


# ── TenantPattern ──────────────────────────────────────────────


class TestTenantPatternModel:
    @pytest.mark.asyncio
    async def test_create_pattern(self, db_session: AsyncSession, seed_tenant):
        pattern = TenantPatternModel(
            tenant_id=TEST_TENANT_ID,
            pattern_type="timing",
            subject_feature="post_hour",
            subject_value="18",
            title="Evening posts perform 2x better",
            explanation="Posts at 18:00 have 2x the engagement rate vs. morning posts",
            confidence=0.85,
            supporting_observation_count=42,
            observation_window_start=_now(),
            observation_window_end=_now(),
        )
        db_session.add(pattern)
        await db_session.commit()

        result = await db_session.execute(
            select(TenantPatternModel).where(TenantPatternModel.id == pattern.id)
        )
        row = result.scalar_one()
        assert row.pattern_type == "timing"
        assert row.confidence == pytest.approx(0.85)
        assert row.is_active is True
        assert row.version == 1
        assert row.source == "tenant"

    @pytest.mark.asyncio
    async def test_supersede_pattern(self, db_session: AsyncSession, seed_tenant):
        old = TenantPatternModel(
            tenant_id=TEST_TENANT_ID,
            pattern_type="content",
            title="Old pattern",
            explanation="...",
            confidence=0.6,
        )
        db_session.add(old)
        await db_session.flush()

        new = TenantPatternModel(
            tenant_id=TEST_TENANT_ID,
            pattern_type="content",
            title="Updated pattern",
            explanation="...",
            confidence=0.9,
            version=2,
            superseded_by_id=None,
        )
        db_session.add(new)
        await db_session.flush()

        old.is_active = False
        old.superseded_by_id = new.id
        await db_session.commit()

        result = await db_session.execute(
            select(TenantPatternModel).where(TenantPatternModel.id == old.id)
        )
        row = result.scalar_one()
        assert row.is_active is False
        assert row.superseded_by_id == new.id


# ── GlobalPatternStat ──────────────────────────────────────────


class TestGlobalPatternStatModel:
    @pytest.mark.asyncio
    async def test_create_without_tenant(self, db_session: AsyncSession, seed_tenant):
        stat = GlobalPatternStatModel(
            feature_name="post_hour",
            feature_value="18",
            action_type="post_published",
            mean_reward=0.72,
            std_dev=0.15,
            confidence_lower=0.65,
            confidence_upper=0.79,
            observation_count=500,
            tenant_count=12,
            k_anonymity_threshold=5,
            computed_at=_now(),
        )
        db_session.add(stat)
        await db_session.commit()

        result = await db_session.execute(
            select(GlobalPatternStatModel).where(GlobalPatternStatModel.id == stat.id)
        )
        row = result.scalar_one()
        assert row.tenant_count == 12
        assert row.mean_reward == pytest.approx(0.72)
        assert not hasattr(row, 'tenant_id') or not hasattr(GlobalPatternStatModel, 'tenant_id')

    @pytest.mark.asyncio
    async def test_unique_feature_action(self, db_session: AsyncSession, seed_tenant):
        from sqlalchemy.exc import IntegrityError

        s1 = GlobalPatternStatModel(
            feature_name="platform",
            feature_value="instagram",
            action_type="post_published",
            mean_reward=0.5,
            observation_count=100,
            tenant_count=10,
            k_anonymity_threshold=5,
            computed_at=_now(),
        )
        db_session.add(s1)
        await db_session.flush()

        s2 = GlobalPatternStatModel(
            feature_name="platform",
            feature_value="instagram",
            action_type="post_published",
            mean_reward=0.6,
            observation_count=200,
            tenant_count=15,
            k_anonymity_threshold=5,
            computed_at=_now(),
        )
        db_session.add(s2)
        with pytest.raises(IntegrityError):
            await db_session.flush()


# ── CohortDefinition ──────────────────────────────────────────


class TestCohortDefinitionModel:
    @pytest.mark.asyncio
    async def test_create_cohort(self, db_session: AsyncSession, seed_tenant):
        cohort = CohortDefinitionModel(
            tenant_id=TEST_TENANT_ID,
            name="Hip-Hop Artists",
            description="Artists in the hip-hop genre with 50+ posts",
            criteria={"genre": "hip-hop", "min_posts": 50},
        )
        db_session.add(cohort)
        await db_session.commit()

        result = await db_session.execute(
            select(CohortDefinitionModel).where(CohortDefinitionModel.id == cohort.id)
        )
        row = result.scalar_one()
        assert row.name == "Hip-Hop Artists"
        assert row.criteria["min_posts"] == 50
        assert row.member_count == 0
        assert row.is_active is True


# ── PromptVersion + PromptAssignment ────────────────────────────


class TestPromptVersionModel:
    @pytest.mark.asyncio
    async def test_create_prompt_version(self, db_session: AsyncSession, seed_tenant):
        pv = PromptVersionModel(
            prompt_key="caption_generator",
            version=1,
            template="Generate a caption for {{content_type}} on {{platform}}",
            system_prompt="You are a social media expert.",
            model_id="claude-sonnet-4-6",
            parameters={"temperature": 0.7},
            description="Initial caption generator prompt",
            author="drew",
        )
        db_session.add(pv)
        await db_session.commit()

        result = await db_session.execute(
            select(PromptVersionModel).where(PromptVersionModel.id == pv.id)
        )
        row = result.scalar_one()
        assert row.prompt_key == "caption_generator"
        assert row.version == 1
        assert row.model_id == "claude-sonnet-4-6"
        assert row.is_active is True

    @pytest.mark.asyncio
    async def test_unique_key_version(self, db_session: AsyncSession, seed_tenant):
        from sqlalchemy.exc import IntegrityError

        pv1 = PromptVersionModel(
            prompt_key="test_prompt",
            version=1,
            template="v1",
        )
        db_session.add(pv1)
        await db_session.flush()

        pv2 = PromptVersionModel(
            prompt_key="test_prompt",
            version=1,
            template="v1 duplicate",
        )
        db_session.add(pv2)
        with pytest.raises(IntegrityError):
            await db_session.flush()


class TestPromptAssignmentModel:
    @pytest.mark.asyncio
    async def test_create_assignment(self, db_session: AsyncSession, seed_tenant):
        pv = PromptVersionModel(
            prompt_key="scheduler",
            version=1,
            template="Schedule posts optimally.",
        )
        db_session.add(pv)
        await db_session.flush()

        assignment = PromptAssignmentModel(
            tenant_id=TEST_TENANT_ID,
            prompt_key="scheduler",
            prompt_version_id=pv.id,
            reason="A/B test group A",
            assigned_by=TEST_USER_ID,
        )
        db_session.add(assignment)
        await db_session.commit()

        result = await db_session.execute(
            select(PromptAssignmentModel).where(PromptAssignmentModel.id == assignment.id)
        )
        row = result.scalar_one()
        assert row.prompt_key == "scheduler"
        assert row.prompt_version_id == pv.id
        assert row.assigned_by == TEST_USER_ID

    @pytest.mark.asyncio
    async def test_unique_tenant_key(self, db_session: AsyncSession, seed_tenant):
        from sqlalchemy.exc import IntegrityError

        pv = PromptVersionModel(
            prompt_key="test_key",
            version=1,
            template="...",
        )
        db_session.add(pv)
        await db_session.flush()

        a1 = PromptAssignmentModel(
            tenant_id=TEST_TENANT_ID,
            prompt_key="test_key",
            prompt_version_id=pv.id,
        )
        db_session.add(a1)
        await db_session.flush()

        a2 = PromptAssignmentModel(
            tenant_id=TEST_TENANT_ID,
            prompt_key="test_key",
            prompt_version_id=pv.id,
        )
        db_session.add(a2)
        with pytest.raises(IntegrityError):
            await db_session.flush()


# ── PromptVersion ↔ PromptAssignment relationship ───────────────


class TestPromptRelationships:
    @pytest.mark.asyncio
    async def test_version_to_assignments(self, db_session: AsyncSession, seed_tenant):
        pv = PromptVersionModel(
            prompt_key="rel_test",
            version=1,
            template="...",
        )
        db_session.add(pv)
        await db_session.flush()

        a1 = PromptAssignmentModel(
            tenant_id=TEST_TENANT_ID,
            prompt_key="rel_test",
            prompt_version_id=pv.id,
        )
        db_session.add(a1)
        await db_session.commit()

        result = await db_session.execute(
            select(PromptVersionModel)
            .where(PromptVersionModel.id == pv.id)
            .options(selectinload(PromptVersionModel.assignments))
        )
        loaded_pv = result.scalar_one()
        assert len(loaded_pv.assignments) == 1
        assert loaded_pv.assignments[0].tenant_id == TEST_TENANT_ID

    @pytest.mark.asyncio
    async def test_assignment_to_version(self, db_session: AsyncSession, seed_tenant):
        pv = PromptVersionModel(
            prompt_key="back_ref_test",
            version=1,
            template="...",
        )
        db_session.add(pv)
        await db_session.flush()

        assignment = PromptAssignmentModel(
            tenant_id=TEST_TENANT_ID,
            prompt_key="back_ref_test",
            prompt_version_id=pv.id,
        )
        db_session.add(assignment)
        await db_session.commit()

        result = await db_session.execute(
            select(PromptAssignmentModel)
            .where(PromptAssignmentModel.id == assignment.id)
            .options(selectinload(PromptAssignmentModel.prompt_version))
        )
        loaded = result.scalar_one()
        assert loaded.prompt_version.prompt_key == "back_ref_test"
        assert loaded.prompt_version.version == 1


# ── EvaluationRun + EvaluationResult ────────────────────────────


class TestEvaluationRunModel:
    @pytest.mark.asyncio
    async def test_create_run_with_metrics(self, db_session: AsyncSession, seed_tenant):
        run = EvaluationRunModel(
            tenant_id=TEST_TENANT_ID,
            evaluation_type="ranking",
            scope="tenant",
            window_start=_now(),
            window_end=_now(),
            sample_size=50,
            mae=0.12,
            rank_correlation=0.78,
            top_k_precision=0.6,
            top_k=3,
            status="completed",
            started_at=_now(),
            completed_at=_now(),
        )
        db_session.add(run)
        await db_session.commit()

        result = await db_session.execute(
            select(EvaluationRunModel).where(EvaluationRunModel.id == run.id)
        )
        row = result.scalar_one()
        assert row.mae == pytest.approx(0.12)
        assert row.rank_correlation == pytest.approx(0.78)
        assert row.sample_size == 50


class TestEvaluationResultModel:
    @pytest.mark.asyncio
    async def test_create_result(self, db_session: AsyncSession, seed_tenant):
        run = EvaluationRunModel(
            tenant_id=TEST_TENANT_ID,
            evaluation_type="timing",
            window_start=_now(),
            window_end=_now(),
        )
        db_session.add(run)
        await db_session.flush()

        result_obj = EvaluationResultModel(
            evaluation_run_id=run.id,
            action_type="post_published",
            predicted_score=0.8,
            predicted_rank=1,
            actual_reward=0.65,
            actual_rank=2,
            absolute_error=0.15,
            explanation={"top_factor": "post_hour"},
        )
        db_session.add(result_obj)
        await db_session.commit()

        result = await db_session.execute(
            select(EvaluationResultModel).where(
                EvaluationResultModel.id == result_obj.id
            )
        )
        row = result.scalar_one()
        assert row.predicted_score == pytest.approx(0.8)
        assert row.absolute_error == pytest.approx(0.15)
        assert row.explanation["top_factor"] == "post_hour"


# ── EvaluationRun ↔ EvaluationResult relationship ──────────────


class TestEvaluationRelationships:
    @pytest.mark.asyncio
    async def test_run_to_results(self, db_session: AsyncSession, seed_tenant):
        run = EvaluationRunModel(
            tenant_id=TEST_TENANT_ID,
            evaluation_type="ranking",
            window_start=_now(),
            window_end=_now(),
            sample_size=3,
        )
        db_session.add(run)
        await db_session.flush()

        for i in range(3):
            db_session.add(EvaluationResultModel(
                evaluation_run_id=run.id,
                action_type="post_published",
                predicted_score=0.9 - i * 0.1,
                predicted_rank=i + 1,
            ))
        await db_session.commit()

        result = await db_session.execute(
            select(EvaluationRunModel)
            .where(EvaluationRunModel.id == run.id)
            .options(selectinload(EvaluationRunModel.results))
        )
        loaded = result.scalar_one()
        assert len(loaded.results) == 3

    @pytest.mark.asyncio
    async def test_cascade_delete(self, db_session: AsyncSession, seed_tenant):
        run = EvaluationRunModel(
            tenant_id=TEST_TENANT_ID,
            evaluation_type="timing",
            window_start=_now(),
            window_end=_now(),
        )
        db_session.add(run)
        await db_session.flush()

        db_session.add(EvaluationResultModel(
            evaluation_run_id=run.id,
            action_type="test",
            predicted_score=0.5,
        ))
        await db_session.commit()

        await db_session.delete(run)
        await db_session.commit()

        result = await db_session.execute(
            select(EvaluationResultModel).where(
                EvaluationResultModel.evaluation_run_id == run.id
            )
        )
        assert len(result.scalars().all()) == 0

    @pytest.mark.asyncio
    async def test_result_back_populates_run(self, db_session: AsyncSession, seed_tenant):
        run = EvaluationRunModel(
            tenant_id=TEST_TENANT_ID,
            evaluation_type="content",
            window_start=_now(),
            window_end=_now(),
        )
        db_session.add(run)
        await db_session.flush()

        result_obj = EvaluationResultModel(
            evaluation_run_id=run.id,
            action_type="test",
            predicted_score=0.7,
        )
        db_session.add(result_obj)
        await db_session.commit()

        result = await db_session.execute(
            select(EvaluationResultModel)
            .where(EvaluationResultModel.id == result_obj.id)
            .options(selectinload(EvaluationResultModel.evaluation_run))
        )
        loaded = result.scalar_one()
        assert loaded.evaluation_run.evaluation_type == "content"


# ── LearningAuditLog ───────────────────────────────────────────


class TestLearningAuditLogModel:
    @pytest.mark.asyncio
    async def test_create_audit_entry(self, db_session: AsyncSession, seed_tenant):
        entry = LearningAuditLogModel(
            tenant_id=TEST_TENANT_ID,
            action="observation_recorded",
            entity_type="learning_event",
            entity_id=uuid.uuid4(),
            user_id=TEST_USER_ID,
            details={"source": "worker", "reward": 0.72},
            explanation="Recorded post outcome after 24h measurement window",
        )
        db_session.add(entry)
        await db_session.commit()

        result = await db_session.execute(
            select(LearningAuditLogModel).where(LearningAuditLogModel.id == entry.id)
        )
        row = result.scalar_one()
        assert row.action == "observation_recorded"
        assert row.details["reward"] == 0.72
        assert row.user_id == TEST_USER_ID

    @pytest.mark.asyncio
    async def test_audit_without_user(self, db_session: AsyncSession, seed_tenant):
        entry = LearningAuditLogModel(
            tenant_id=TEST_TENANT_ID,
            action="global_prior_computed",
            entity_type="global_pattern_stat",
            details={"tenant_count": 12},
        )
        db_session.add(entry)
        await db_session.commit()

        result = await db_session.execute(
            select(LearningAuditLogModel).where(LearningAuditLogModel.id == entry.id)
        )
        row = result.scalar_one()
        assert row.user_id is None
        assert row.entity_id is None


# ── Pydantic schema round-trip ──────────────────────────────────


class TestPydanticSchemas:
    @pytest.mark.asyncio
    async def test_learning_event_serializes(self, db_session: AsyncSession, seed_tenant):
        try:
            from app.schemas.learning import LearningEventResponse
        except ModuleNotFoundError:
            pytest.skip("app.schemas not importable from root — run from apps/api")

        event = LearningEventModel(
            tenant_id=TEST_TENANT_ID,
            action_type="test_serialization",
            observed_at=_now(),
            features={"key": "value"},
        )
        db_session.add(event)
        await db_session.commit()
        await db_session.refresh(event)

        response = LearningEventResponse.model_validate(event)
        assert response.action_type == "test_serialization"
        assert response.features == {"key": "value"}

    @pytest.mark.asyncio
    async def test_post_outcome_serializes(self, db_session: AsyncSession, seed_channel):
        try:
            from app.schemas.learning import PostOutcomeResponse
        except ModuleNotFoundError:
            pytest.skip("app.schemas not importable from root — run from apps/api")
        from amplify.db.models.post import PostModel

        post = PostModel(
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            channel_id=seed_channel.id,
            platform="instagram",
        )
        db_session.add(post)
        await db_session.flush()

        outcome = PostOutcomeModel(
            tenant_id=TEST_TENANT_ID,
            post_id=post.id,
            measurement_window="24h",
            impressions=1000,
            engagement_rate=0.05,
            measured_at=_now(),
        )
        db_session.add(outcome)
        await db_session.commit()
        await db_session.refresh(outcome)

        response = PostOutcomeResponse.model_validate(outcome)
        assert response.impressions == 1000
        assert response.engagement_rate == pytest.approx(0.05)

    @pytest.mark.asyncio
    async def test_evaluation_run_serializes(self, db_session: AsyncSession, seed_tenant):
        try:
            from app.schemas.learning import EvaluationRunResponse
        except ModuleNotFoundError:
            pytest.skip("app.schemas not importable from root — run from apps/api")

        run = EvaluationRunModel(
            tenant_id=TEST_TENANT_ID,
            evaluation_type="ranking",
            window_start=_now(),
            window_end=_now(),
            sample_size=25,
            mae=0.1,
        )
        db_session.add(run)
        await db_session.commit()
        await db_session.refresh(run)

        response = EvaluationRunResponse.model_validate(run)
        assert response.evaluation_type == "ranking"
        assert response.mae == pytest.approx(0.1)

    @pytest.mark.asyncio
    async def test_tenant_pattern_serializes(self, db_session: AsyncSession, seed_tenant):
        try:
            from app.schemas.learning import TenantPatternResponse
        except ModuleNotFoundError:
            pytest.skip("app.schemas not importable from root — run from apps/api")

        pattern = TenantPatternModel(
            tenant_id=TEST_TENANT_ID,
            pattern_type="timing",
            title="Evening posts rock",
            explanation="...",
            confidence=0.9,
        )
        db_session.add(pattern)
        await db_session.commit()
        await db_session.refresh(pattern)

        response = TenantPatternResponse.model_validate(pattern)
        assert response.pattern_type == "timing"
        assert response.confidence == pytest.approx(0.9)

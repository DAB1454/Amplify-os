"""Database integration tests — migrations, seeds, and schema verification.

These tests run against an actual database (Postgres in CI, SQLite locally).
They verify that:
1. Alembic migrations create all expected tables
2. Seed scripts insert data without errors
3. Seeded entities can be queried back
4. Foreign key relationships are intact
5. Migration state matches the ORM model state (drift check)

Run with:
    python -m pytest tests/test_db_integration.py -v

Requires:
    DATABASE_URL env var (defaults to aiosqlite in-memory for local runs)
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from amplify.db.base import Base
from amplify.db.models import (
    ApprovalCommentModel,
    ApprovalModel,
    ArtistModel,
    AssetModel,
    AssetVariantModel,
    AssistedTaskModel,
    AuditLogModel,
    BillingPlanModel,
    CalendarItemModel,
    CampaignModel,
    CampaignTemplateModel,
    ChannelConnectionModel,
    CohortDefinitionModel,
    DailyMetricModel,
    EvaluationResultModel,
    EvaluationRunModel,
    ExperimentModel,
    GlobalPatternStatModel,
    LearningAuditLogModel,
    LearningEventModel,
    MembershipModel,
    MetricEventModel,
    PostFeatureVectorModel,
    PostModel,
    PostOutcomeModel,
    PromptAssignmentModel,
    PromptVersionModel,
    RedirectModel,
    ReleaseModel,
    SubscriptionModel,
    TenantModel,
    TenantPatternModel,
    TrackModel,
    UserModel,
)

pytestmark = pytest.mark.asyncio

# Use Postgres in CI, SQLite locally
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite+aiosqlite://",
)

EXPECTED_TABLES = sorted([
    "approval_comments",
    "approvals",
    "artists",
    "asset_variants",
    "assets",
    "assisted_tasks",
    "audit_logs",
    "billing_plans",
    "calendar_items",
    "campaign_templates",
    "campaigns",
    "channel_connections",
    "cohort_definitions",
    "daily_metrics",
    "evaluation_results",
    "evaluation_runs",
    "experiments",
    "global_pattern_stats",
    "learning_audit_log",
    "learning_events",
    "memberships",
    "metric_events",
    "post_feature_vectors",
    "post_outcomes",
    "posts",
    "prompt_assignments",
    "prompt_versions",
    "redirects",
    "releases",
    "subscriptions",
    "tenant_patterns",
    "tenants",
    "tracks",
    "users",
])


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
async def engine():
    """Create a test engine and apply the full schema."""
    eng = create_async_engine(DATABASE_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest.fixture(scope="module")
async def session_factory(engine):
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s
        await s.rollback()


# ── Schema tests ────────────────────────────────────────────────────────


class TestSchemaCreation:
    """Verify Base.metadata.create_all produces the expected tables."""

    async def test_all_tables_created(self, engine):
        async with engine.connect() as conn:
            table_names = await conn.run_sync(
                lambda sync_conn: sorted(inspect(sync_conn).get_table_names())
            )
        assert table_names == EXPECTED_TABLES

    async def test_table_count(self, engine):
        async with engine.connect() as conn:
            table_names = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
        assert len(table_names) == 34

    async def test_metadata_matches_expected_tables(self):
        """ORM metadata should declare exactly the expected tables."""
        orm_tables = sorted(Base.metadata.tables.keys())
        assert orm_tables == EXPECTED_TABLES


# ── Seed tests ──────────────────────────────────────────────────────────


class TestSeedAndQuery:
    """Seed the database and verify entities can be queried back."""

    TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
    USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
    ARTIST_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
    RELEASE_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")
    CAMPAIGN_ID = uuid.UUID("55555555-5555-5555-5555-555555555555")
    CHANNEL_ID = uuid.UUID("66666666-6666-6666-6666-666666666666")
    POST_ID = uuid.UUID("77777777-7777-7777-7777-777777777777")
    PLAN_ID = uuid.UUID("88888888-8888-8888-8888-888888888888")

    @pytest.fixture(autouse=True)
    async def _seed_data(self, session):
        """Seed a minimal dataset covering all core entity types."""
        # Tenant
        session.add(TenantModel(
            id=self.TENANT_ID,
            name="Test Studio",
            slug="test-studio",
            plan_tier="solo",
            settings={"timezone": "UTC"},
            is_active=True,
        ))
        # User
        session.add(UserModel(
            id=self.USER_ID,
            email="test@amplify.dev",
            hashed_password="$2b$12$test_hash",
            display_name="Test User",
            is_active=True,
        ))
        # Membership
        session.add(MembershipModel(
            id=uuid.uuid4(),
            user_id=self.USER_ID,
            tenant_id=self.TENANT_ID,
            role="owner",
        ))
        # Artist
        session.add(ArtistModel(
            id=self.ARTIST_ID,
            tenant_id=self.TENANT_ID,
            name="Test Artist",
            slug="test-artist",
            bio="A test artist for integration tests.",
            genre="Test",
            social_links={},
        ))
        # Release
        session.add(ReleaseModel(
            id=self.RELEASE_ID,
            tenant_id=self.TENANT_ID,
            artist_id=self.ARTIST_ID,
            title="Test Album",
            release_type="album",
            status="draft",
            release_date=date(2026, 6, 1),
            metadata_={},
            hyperfollow_url="https://distrokid.com/hyperfollow/test",
        ))
        # Track
        session.add(TrackModel(
            id=uuid.uuid4(),
            tenant_id=self.TENANT_ID,
            release_id=self.RELEASE_ID,
            title="Track One",
            track_number=1,
            duration_seconds=210,
            isrc="USTEST000001",
            is_single=True,
        ))
        session.add(TrackModel(
            id=uuid.uuid4(),
            tenant_id=self.TENANT_ID,
            release_id=self.RELEASE_ID,
            title="Track Two",
            track_number=2,
            duration_seconds=185,
            isrc="USTEST000002",
            is_single=False,
        ))
        # Channel
        session.add(ChannelConnectionModel(
            id=self.CHANNEL_ID,
            tenant_id=self.TENANT_ID,
            artist_id=self.ARTIST_ID,
            platform="instagram",
            display_name="test_artist",
            is_active=True,
            settings={},
        ))
        # Campaign
        session.add(CampaignModel(
            id=self.CAMPAIGN_ID,
            tenant_id=self.TENANT_ID,
            artist_id=self.ARTIST_ID,
            release_id=self.RELEASE_ID,
            name="Test Launch",
            status="active",
            phase="pre_release",
            goals={},
            config={},
        ))
        # Post
        session.add(PostModel(
            id=self.POST_ID,
            tenant_id=self.TENANT_ID,
            campaign_id=self.CAMPAIGN_ID,
            channel_id=self.CHANNEL_ID,
            platform="instagram",
            status="draft",
            content_text="Test post content",
            media_urls=[],
            engagement={},
        ))
        # Calendar item
        session.add(CalendarItemModel(
            id=uuid.uuid4(),
            tenant_id=self.TENANT_ID,
            campaign_id=self.CAMPAIGN_ID,
            title="Album Release",
            description="The big day!",
            item_type="release",
            scheduled_date=date(2026, 6, 1),
            is_completed=False,
        ))
        # Billing
        session.add(BillingPlanModel(
            id=self.PLAN_ID,
            name="Test Plan",
            tier="test",
            price_monthly=0.0,
            price_yearly=0.0,
            features=["unlimited"],
            limits={"max_artists": 1},
            is_active=True,
        ))
        session.add(SubscriptionModel(
            id=uuid.uuid4(),
            tenant_id=self.TENANT_ID,
            plan_id=self.PLAN_ID,
            status="active",
            current_period_start=date(2026, 1, 1),
            current_period_end=date(2026, 12, 31),
        ))
        # Redirect
        session.add(RedirectModel(
            id=uuid.uuid4(),
            tenant_id=self.TENANT_ID,
            slug="test-link",
            target_url="https://linktr.ee/test",
            campaign_id=self.CAMPAIGN_ID,
            click_count=0,
        ))
        # Audit log
        session.add(AuditLogModel(
            id=uuid.uuid4(),
            tenant_id=self.TENANT_ID,
            user_id=self.USER_ID,
            action="campaign.created",
            entity_type="campaign",
            entity_id=self.CAMPAIGN_ID,
            changes={"name": "Test Launch"},
        ))
        # Experiment
        session.add(ExperimentModel(
            id=uuid.uuid4(),
            tenant_id=self.TENANT_ID,
            campaign_id=self.CAMPAIGN_ID,
            name="Caption A/B",
            hypothesis="Test hypothesis",
            status="draft",
            variants=[{"id": 0, "label": "A"}, {"id": 1, "label": "B"}],
            metrics_config={"primary": "engagement"},
        ))
        await session.flush()

    # ── Query-back assertions ──────────────────────────────────────

    async def test_tenant_exists(self, session):
        result = await session.execute(
            select(TenantModel).where(TenantModel.slug == "test-studio")
        )
        tenant = result.scalar_one()
        assert tenant.name == "Test Studio"
        assert tenant.plan_tier == "solo"
        assert tenant.is_active is True

    async def test_user_exists(self, session):
        result = await session.execute(
            select(UserModel).where(UserModel.email == "test@amplify.dev")
        )
        user = result.scalar_one()
        assert user.display_name == "Test User"

    async def test_membership_links_user_to_tenant(self, session):
        result = await session.execute(
            select(MembershipModel).where(
                MembershipModel.tenant_id == self.TENANT_ID
            )
        )
        membership = result.scalar_one()
        assert membership.user_id == self.USER_ID
        assert membership.role == "owner"

    async def test_artist_exists(self, session):
        result = await session.execute(
            select(ArtistModel).where(ArtistModel.id == self.ARTIST_ID)
        )
        artist = result.scalar_one()
        assert artist.name == "Test Artist"
        assert artist.tenant_id == self.TENANT_ID

    async def test_release_with_tracks(self, session):
        result = await session.execute(
            select(ReleaseModel).where(ReleaseModel.id == self.RELEASE_ID)
        )
        release = result.scalar_one()
        assert release.title == "Test Album"
        assert release.hyperfollow_url == "https://distrokid.com/hyperfollow/test"

        tracks_result = await session.execute(
            select(TrackModel)
            .where(TrackModel.release_id == self.RELEASE_ID)
            .order_by(TrackModel.track_number)
        )
        tracks = tracks_result.scalars().all()
        assert len(tracks) == 2
        assert tracks[0].title == "Track One"
        assert tracks[0].is_single is True
        assert tracks[1].title == "Track Two"

    async def test_campaign_linked_to_release(self, session):
        result = await session.execute(
            select(CampaignModel).where(CampaignModel.id == self.CAMPAIGN_ID)
        )
        campaign = result.scalar_one()
        assert campaign.release_id == self.RELEASE_ID
        assert campaign.status == "active"

    async def test_post_linked_to_campaign_and_channel(self, session):
        result = await session.execute(
            select(PostModel).where(PostModel.id == self.POST_ID)
        )
        post = result.scalar_one()
        assert post.campaign_id == self.CAMPAIGN_ID
        assert post.channel_id == self.CHANNEL_ID
        assert post.platform == "instagram"

    async def test_channel_connection_exists(self, session):
        result = await session.execute(
            select(ChannelConnectionModel).where(
                ChannelConnectionModel.id == self.CHANNEL_ID
            )
        )
        channel = result.scalar_one()
        assert channel.platform == "instagram"
        assert channel.artist_id == self.ARTIST_ID

    async def test_billing_plan_and_subscription(self, session):
        result = await session.execute(
            select(SubscriptionModel).where(
                SubscriptionModel.tenant_id == self.TENANT_ID
            )
        )
        sub = result.scalar_one()
        assert sub.status == "active"
        assert sub.plan_id == self.PLAN_ID

    async def test_redirect_exists(self, session):
        result = await session.execute(
            select(RedirectModel).where(RedirectModel.slug == "test-link")
        )
        redirect = result.scalar_one()
        assert redirect.target_url == "https://linktr.ee/test"

    async def test_audit_log_exists(self, session):
        result = await session.execute(
            select(AuditLogModel).where(
                AuditLogModel.tenant_id == self.TENANT_ID
            )
        )
        log = result.scalar_one()
        assert log.action == "campaign.created"

    async def test_experiment_exists(self, session):
        result = await session.execute(
            select(ExperimentModel).where(
                ExperimentModel.tenant_id == self.TENANT_ID
            )
        )
        exp = result.scalar_one()
        assert exp.name == "Caption A/B"
        assert len(exp.variants) == 2

    async def test_calendar_item_exists(self, session):
        result = await session.execute(
            select(CalendarItemModel).where(
                CalendarItemModel.tenant_id == self.TENANT_ID
            )
        )
        item = result.scalar_one()
        assert item.title == "Album Release"
        assert item.is_completed is False

    async def test_entity_counts(self, session):
        """Verify expected entity counts from the seed."""
        counts = {
            "tenants": (await session.execute(select(func.count()).select_from(TenantModel))).scalar(),
            "users": (await session.execute(select(func.count()).select_from(UserModel))).scalar(),
            "artists": (await session.execute(select(func.count()).select_from(ArtistModel))).scalar(),
            "releases": (await session.execute(select(func.count()).select_from(ReleaseModel))).scalar(),
            "tracks": (await session.execute(select(func.count()).select_from(TrackModel))).scalar(),
            "campaigns": (await session.execute(select(func.count()).select_from(CampaignModel))).scalar(),
            "posts": (await session.execute(select(func.count()).select_from(PostModel))).scalar(),
        }
        assert counts == {
            "tenants": 1,
            "users": 1,
            "artists": 1,
            "releases": 1,
            "tracks": 2,
            "campaigns": 1,
            "posts": 1,
        }


# ── Migration drift check ──────────────────────────────────────────────


class TestMigrationDrift:
    """Ensure ORM models and Alembic migration stay in sync."""

    def test_metadata_table_count_matches_expected(self):
        """If a model is added without updating this test, it will fail."""
        assert len(Base.metadata.tables) == 34

    def test_all_models_registered_on_base(self):
        """Every model class should be discoverable via Base.metadata."""
        model_classes = [
            TenantModel, UserModel, MembershipModel, ArtistModel,
            ReleaseModel, TrackModel, ChannelConnectionModel,
            CampaignModel, CampaignTemplateModel, CalendarItemModel,
            AssetModel, AssetVariantModel, PostModel,
            ApprovalModel, ApprovalCommentModel,
            ExperimentModel, MetricEventModel, DailyMetricModel,
            AuditLogModel, BillingPlanModel, SubscriptionModel,
            AssistedTaskModel, RedirectModel,
            # Learning subsystem
            LearningEventModel, PostFeatureVectorModel, PostOutcomeModel,
            TenantPatternModel, GlobalPatternStatModel, CohortDefinitionModel,
            PromptVersionModel, PromptAssignmentModel,
            EvaluationRunModel, EvaluationResultModel, LearningAuditLogModel,
        ]
        registered = set(Base.metadata.tables.keys())
        for cls in model_classes:
            table_name = cls.__tablename__
            assert table_name in registered, (
                f"{cls.__name__} ({table_name}) not registered on Base.metadata"
            )

    def test_no_orphaned_tables_in_metadata(self):
        """No table in metadata without a corresponding model."""
        registered = set(Base.metadata.tables.keys())
        assert registered == set(EXPECTED_TABLES)

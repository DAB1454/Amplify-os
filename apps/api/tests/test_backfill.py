"""Tests for historical backfill and offline learning bootstrap.

Covers:
- BackfillEngine: feature extraction, outcome computation, event emission
- Duplicate safety: re-running backfill skips existing records
- Resumability: cursor-based pagination
- Data quality warnings
- Pattern bootstrap after backfill
- Dry run mode
- BackfillReport aggregation
- Backfill isolation (source tagging)
"""

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEPLOYMENT_MODE", "local")
os.environ.setdefault("REDIS_URL", "")

from amplify.db.base import Base
import amplify.db.models  # noqa: F401 — register all ORM models

from amplify.db.models.tenant import TenantModel
from amplify.db.models.artist import ArtistModel
from amplify.db.models.channel import ChannelConnectionModel
from amplify.db.models.post import PostModel
from amplify.db.models.learning import (
    LearningEventModel,
    PostFeatureVectorModel,
    PostOutcomeModel,
)
from amplify.learning.backfill import BackfillEngine, BackfillReport

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
TEST_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def _json_serializer(obj):
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj).__name__} not serializable")


def _dumps(value):
    return json.dumps(value, default=_json_serializer)


# ── Fake services (protocol-compatible) ──────────────────────────


class FakeFeatureExtractor:
    """Extracts features by creating a minimal PostFeatureVector."""

    def __init__(self, db: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id

    async def extract_and_persist(self, post_id: uuid.UUID):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        vec = PostFeatureVectorModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            post_id=post_id,
            features={"platform": "instagram", "post_hour": 18, "caption_length": 50},
            platform="instagram",
            post_hour=18,
            caption_length=50,
            extracted_at=now,
            schema_version=1,
        )
        self.db.add(vec)
        await self.db.flush()
        return vec


class FakeOutcomeRecorder:
    """Records outcomes by creating a minimal PostOutcome."""

    def __init__(self, db: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id

    async def record_outcome(
        self, post_id, measurement_window, metrics, measured_at=None, source=""
    ):
        now = measured_at or datetime.now(timezone.utc).replace(tzinfo=None)
        outcome = PostOutcomeModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            post_id=post_id,
            measurement_window=measurement_window,
            impressions=metrics.get("impressions"),
            reach=metrics.get("reach"),
            engagements=metrics.get("engagements"),
            saves=metrics.get("saves"),
            shares=metrics.get("shares"),
            clicks=metrics.get("clicks"),
            reward=0.5,
            reward_breakdown={"formula_version": "test"},
            measured_at=now,
            source=source,
        )
        self.db.add(outcome)
        await self.db.flush()
        return outcome


class FakeEventEmitter:
    """Emits events by creating a minimal LearningEvent."""

    def __init__(self, db: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id

    async def emit(self, action_type, observed_at=None, **kwargs):
        idem_key = kwargs.get("idempotency_key", f"{action_type}:{uuid.uuid4()}")
        # Check dedup
        existing = await self.db.execute(
            select(LearningEventModel).where(
                LearningEventModel.idempotency_key == idem_key
            )
        )
        if existing.scalar_one_or_none():
            return  # skip duplicate
        now = observed_at or datetime.now(timezone.utc).replace(tzinfo=None)
        event = LearningEventModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            action_type=action_type,
            post_id=kwargs.get("post_id"),
            campaign_id=kwargs.get("campaign_id"),
            platform=kwargs.get("platform"),
            features=kwargs.get("features", {}),
            outcomes=kwargs.get("outcomes", {}),
            reward=kwargs.get("reward"),
            reward_breakdown=kwargs.get("reward_breakdown"),
            outcome_measured_at=kwargs.get("outcome_measured_at"),
            observed_at=now,
            source=kwargs.get("source", ""),
            confidence=kwargs.get("confidence", 1.0),
            idempotency_key=idem_key,
            schema_version=kwargs.get("schema_version", 1),
        )
        self.db.add(event)
        await self.db.flush()
        return event


class FakePatternUpdater:
    """Fake pattern updater with configurable has_enough_data."""

    def __init__(self, has_enough: bool = False) -> None:
        self._has_enough = has_enough
        self.updated = False

    async def get_data_status(self):
        return {
            "feature_vectors": 10 if self._has_enough else 2,
            "outcomes": 10 if self._has_enough else 2,
            "active_patterns": 0,
            "min_required": 5,
            "has_enough_data": self._has_enough,
        }

    async def update_patterns(self):
        self.updated = True
        return []


# ── Fixtures ─────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False, json_serializer=_dumps)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(
        bind=db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def seed_tenant(db_session: AsyncSession):
    tenant = TenantModel(id=TEST_TENANT_ID, name="Test Tenant", slug="test-tenant")
    db_session.add(tenant)
    await db_session.flush()
    return tenant


@pytest_asyncio.fixture
async def seed_artist(db_session: AsyncSession, seed_tenant):
    artist = ArtistModel(
        id=uuid.uuid4(), tenant_id=TEST_TENANT_ID,
        name="Test Artist", slug="test-artist",
    )
    db_session.add(artist)
    await db_session.flush()
    return artist


@pytest_asyncio.fixture
async def seed_channel(db_session: AsyncSession, seed_artist):
    channel = ChannelConnectionModel(
        id=uuid.uuid4(), tenant_id=TEST_TENANT_ID,
        artist_id=seed_artist.id, platform="instagram",
        integration_mode="automatic",
    )
    db_session.add(channel)
    await db_session.flush()
    return channel


def _make_engine(db_session, tenant_id=TEST_TENANT_ID, dry_run=False):
    """Create a BackfillEngine with fake services."""
    engine = BackfillEngine(db_session, tenant_id, dry_run=dry_run)
    engine.feature_extractor = FakeFeatureExtractor(db_session, tenant_id)
    engine.outcome_recorder = FakeOutcomeRecorder(db_session, tenant_id)
    engine.event_emitter = FakeEventEmitter(db_session, tenant_id)
    engine.pattern_updater = FakePatternUpdater(has_enough=False)
    return engine


@pytest_asyncio.fixture
async def seed_posts(db_session: AsyncSession, seed_channel):
    """Create 5 published posts with engagement data."""
    posts = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for i in range(5):
        post = PostModel(
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            channel_id=seed_channel.id,
            platform="instagram",
            status="published",
            content_text=f"Test post {i} with #hashtag",
            media_urls=[],
            published_at=now - timedelta(days=10 - i),
            engagement={
                "impressions": 1000 + i * 200,
                "reach": 800 + i * 150,
                "engagements": 50 + i * 10,
                "saves": 5 + i,
                "shares": 3 + i,
                "clicks": 10 + i * 2,
            },
        )
        db_session.add(post)
        posts.append(post)
    await db_session.flush()
    return posts


@pytest_asyncio.fixture
async def seed_draft_posts(db_session: AsyncSession, seed_channel):
    posts = []
    for i in range(2):
        post = PostModel(
            id=uuid.uuid4(), tenant_id=TEST_TENANT_ID,
            channel_id=seed_channel.id, platform="instagram",
            status="draft", content_text=f"Draft {i}",
        )
        db_session.add(post)
        posts.append(post)
    await db_session.flush()
    return posts


@pytest_asyncio.fixture
async def seed_posts_no_engagement(db_session: AsyncSession, seed_channel):
    posts = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for i in range(3):
        post = PostModel(
            id=uuid.uuid4(), tenant_id=TEST_TENANT_ID,
            channel_id=seed_channel.id, platform="instagram",
            status="published", content_text=f"No engagement {i}",
            published_at=now - timedelta(days=5),
        )
        db_session.add(post)
        posts.append(post)
    await db_session.flush()
    return posts


# ── BackfillEngine tests ─────────────────────────────────────────


class TestBackfillEngine:

    @pytest.mark.asyncio
    async def test_backfill_creates_feature_vectors(self, db_session, seed_posts):
        engine = _make_engine(db_session)
        report = await engine.run(
            skip_outcomes=True, skip_events=True, bootstrap_patterns=False
        )
        assert report.status == "completed"
        assert report.posts_scanned == 5
        assert report.features_created == 5

        result = await db_session.execute(
            select(PostFeatureVectorModel).where(
                PostFeatureVectorModel.tenant_id == TEST_TENANT_ID
            )
        )
        assert len(list(result.scalars().all())) == 5

    @pytest.mark.asyncio
    async def test_backfill_creates_outcomes(self, db_session, seed_posts):
        engine = _make_engine(db_session)
        report = await engine.run(
            skip_features=True, skip_events=True, bootstrap_patterns=False
        )
        assert report.outcomes_created == 5

        result = await db_session.execute(
            select(PostOutcomeModel).where(
                PostOutcomeModel.tenant_id == TEST_TENANT_ID
            )
        )
        outcomes = list(result.scalars().all())
        assert len(outcomes) == 5
        assert all(o.reward is not None for o in outcomes)
        assert all(o.source == "backfill" for o in outcomes)

    @pytest.mark.asyncio
    async def test_backfill_creates_learning_events(self, db_session, seed_posts):
        engine = _make_engine(db_session)
        # First backfill features + outcomes
        r1 = await engine.run(skip_events=True, bootstrap_patterns=False)
        assert r1.features_created == 5
        assert r1.outcomes_created == 5

        # Then events
        r2 = await engine.run(
            skip_features=True, skip_outcomes=True, bootstrap_patterns=False
        )
        assert r2.events_created == 5

        result = await db_session.execute(
            select(LearningEventModel).where(
                LearningEventModel.tenant_id == TEST_TENANT_ID
            )
        )
        events = list(result.scalars().all())
        assert len(events) == 5
        assert all(e.source == "backfill" for e in events)
        assert all(e.confidence == 0.8 for e in events)

    @pytest.mark.asyncio
    async def test_backfill_full_pipeline(self, db_session, seed_posts):
        engine = _make_engine(db_session)
        report = await engine.run(bootstrap_patterns=False)
        assert report.status == "completed"
        assert report.features_created == 5
        assert report.outcomes_created == 5
        assert report.events_created == 5


class TestBackfillDuplicateSafety:

    @pytest.mark.asyncio
    async def test_features_not_duplicated(self, db_session, seed_posts):
        engine = _make_engine(db_session)
        r1 = await engine.run(
            skip_outcomes=True, skip_events=True, bootstrap_patterns=False
        )
        assert r1.features_created == 5

        r2 = await engine.run(
            skip_outcomes=True, skip_events=True, bootstrap_patterns=False
        )
        assert r2.features_created == 0
        assert r2.features_skipped == 5

    @pytest.mark.asyncio
    async def test_outcomes_not_duplicated(self, db_session, seed_posts):
        engine = _make_engine(db_session)
        r1 = await engine.run(
            skip_features=True, skip_events=True, bootstrap_patterns=False
        )
        assert r1.outcomes_created == 5

        r2 = await engine.run(
            skip_features=True, skip_events=True, bootstrap_patterns=False
        )
        assert r2.outcomes_created == 0
        assert r2.outcomes_skipped == 5

    @pytest.mark.asyncio
    async def test_events_not_duplicated(self, db_session, seed_posts):
        engine = _make_engine(db_session)
        await engine.run(bootstrap_patterns=False)

        r2 = await engine.run(
            skip_features=True, skip_outcomes=True, bootstrap_patterns=False
        )
        assert r2.events_created == 0
        assert r2.events_skipped == 5


class TestBackfillResumability:

    @pytest.mark.asyncio
    async def test_cursor_resumes_from_position(self, db_session, seed_posts):
        engine = _make_engine(db_session)

        r1 = await engine.run(
            batch_size=3, skip_outcomes=True, skip_events=True,
            bootstrap_patterns=False,
        )
        assert r1.posts_scanned == 3
        assert r1.features_created == 3
        assert r1.is_complete is False
        assert r1.cursor is not None

        r2 = await engine.run(
            batch_size=3, cursor=r1.cursor,
            skip_outcomes=True, skip_events=True,
            bootstrap_patterns=False,
        )
        assert r2.posts_scanned == 2
        assert r2.features_created == 2
        assert r2.is_complete is True

        result = await db_session.execute(
            select(PostFeatureVectorModel).where(
                PostFeatureVectorModel.tenant_id == TEST_TENANT_ID
            )
        )
        assert len(list(result.scalars().all())) == 5

    @pytest.mark.asyncio
    async def test_empty_cursor_starts_from_beginning(self, db_session, seed_posts):
        engine = _make_engine(db_session)
        r = await engine.run(
            cursor=None, skip_outcomes=True, skip_events=True,
            bootstrap_patterns=False,
        )
        assert r.posts_scanned == 5


class TestDataQualityWarnings:

    @pytest.mark.asyncio
    async def test_warns_on_missing_engagement(self, db_session, seed_posts_no_engagement):
        engine = _make_engine(db_session)
        report = await engine.run(
            skip_features=True, skip_events=True, bootstrap_patterns=False
        )
        assert any("no engagement data" in w for w in report.warnings)

    @pytest.mark.asyncio
    async def test_warns_on_missing_published_at(self, db_session, seed_channel):
        post = PostModel(
            id=uuid.uuid4(), tenant_id=TEST_TENANT_ID,
            channel_id=seed_channel.id, platform="instagram",
            status="published", content_text="No published_at",
        )
        db_session.add(post)
        await db_session.flush()

        engine = _make_engine(db_session)
        report = await engine.run(
            skip_outcomes=True, skip_events=True, bootstrap_patterns=False
        )
        assert any("missing published_at" in w for w in report.warnings)

    @pytest.mark.asyncio
    async def test_no_posts_warning(self, db_session, seed_tenant):
        engine = _make_engine(db_session)
        report = await engine.run(bootstrap_patterns=False)
        assert report.posts_scanned == 0
        assert any("No posts found" in w for w in report.warnings)
        assert report.is_complete is True


class TestDryRun:

    @pytest.mark.asyncio
    async def test_dry_run_no_writes(self, db_session, seed_posts):
        engine = _make_engine(db_session, dry_run=True)
        report = await engine.run(bootstrap_patterns=False)
        assert report.features_created == 5
        assert report.outcomes_created == 5

        result = await db_session.execute(
            select(PostFeatureVectorModel).where(
                PostFeatureVectorModel.tenant_id == TEST_TENANT_ID
            )
        )
        assert len(list(result.scalars().all())) == 0


class TestPatternBootstrap:

    @pytest.mark.asyncio
    async def test_bootstrap_after_backfill(self, db_session, seed_posts):
        engine = _make_engine(db_session)
        engine.pattern_updater = FakePatternUpdater(has_enough=True)
        report = await engine.run(bootstrap_patterns=True)
        assert report.status == "completed"
        assert engine.pattern_updater.updated is True

    @pytest.mark.asyncio
    async def test_bootstrap_warns_when_insufficient_data(self, db_session, seed_channel):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for i in range(2):
            db_session.add(PostModel(
                id=uuid.uuid4(), tenant_id=TEST_TENANT_ID,
                channel_id=seed_channel.id, platform="instagram",
                status="published", content_text=f"Post {i}",
                published_at=now - timedelta(days=i),
                engagement={"impressions": 100, "engagements": 10},
            ))
        await db_session.flush()

        engine = _make_engine(db_session)
        engine.pattern_updater = FakePatternUpdater(has_enough=False)
        report = await engine.run(bootstrap_patterns=True)
        assert any("Not enough data" in w for w in report.warnings)


class TestBackfillReport:

    def test_report_to_dict(self):
        report = BackfillReport(tenant_id="test", status="completed", posts_scanned=10)
        d = report.to_dict()
        assert d["tenant_id"] == "test"
        assert d["posts_scanned"] == 10
        assert isinstance(d["warnings"], list)

    def test_report_add_warning_dedup(self):
        report = BackfillReport()
        report.add_warning("duplicate")
        report.add_warning("duplicate")
        assert len(report.warnings) == 1

    def test_report_add_error(self):
        report = BackfillReport()
        report.add_error("post-1", "features", "not found")
        assert len(report.errors) == 1
        assert report.errors[0]["stage"] == "features"


class TestInferMeasurementWindow:

    def test_old_post_gets_7d(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        post = MagicMock()
        post.published_at = now - timedelta(days=14)
        assert BackfillEngine._infer_measurement_window(post) == "7d"

    def test_recent_post_gets_24h(self):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        post = MagicMock()
        post.published_at = now - timedelta(hours=30)
        assert BackfillEngine._infer_measurement_window(post) == "24h"

    def test_no_published_at_gets_final(self):
        post = MagicMock()
        post.published_at = None
        assert BackfillEngine._infer_measurement_window(post) == "final"


class TestDraftPostExclusion:

    @pytest.mark.asyncio
    async def test_drafts_excluded(self, db_session, seed_posts, seed_draft_posts):
        engine = _make_engine(db_session)
        report = await engine.run(
            skip_outcomes=True, skip_events=True, bootstrap_patterns=False
        )
        assert report.posts_scanned == 5


class TestBackfillIsolation:

    @pytest.mark.asyncio
    async def test_outcomes_tagged_as_backfill(self, db_session, seed_posts):
        engine = _make_engine(db_session)
        await engine.run(skip_features=True, skip_events=True, bootstrap_patterns=False)

        result = await db_session.execute(
            select(PostOutcomeModel).where(
                PostOutcomeModel.tenant_id == TEST_TENANT_ID
            )
        )
        for o in result.scalars().all():
            assert o.source == "backfill"

    @pytest.mark.asyncio
    async def test_events_tagged_as_backfill(self, db_session, seed_posts):
        engine = _make_engine(db_session)
        await engine.run(bootstrap_patterns=False)

        result = await db_session.execute(
            select(LearningEventModel).where(
                LearningEventModel.tenant_id == TEST_TENANT_ID
            )
        )
        for e in result.scalars().all():
            assert e.source == "backfill"

    @pytest.mark.asyncio
    async def test_events_have_lower_confidence(self, db_session, seed_posts):
        engine = _make_engine(db_session)
        await engine.run(bootstrap_patterns=False)

        result = await db_session.execute(
            select(LearningEventModel).where(
                LearningEventModel.tenant_id == TEST_TENANT_ID
            )
        )
        for e in result.scalars().all():
            assert e.confidence == 0.8

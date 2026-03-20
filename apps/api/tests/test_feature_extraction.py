"""Tests for the feature extraction pipeline and service.

Covers:
- Pure extractor functions (unit tests, no DB)
- Pipeline composition (PostContext → features dict)
- FeatureExtractionService (DB integration tests)
- API endpoints for extraction and backfill
- Feature schema validation
- Example feature rows for documentation
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.learning import PostFeatureVectorModel

# Fixed test IDs matching TenantMiddleware local mode
TEST_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Seed data fixtures ───────────────────────────────────────────


@pytest_asyncio.fixture
async def seed_full_context(db_session: AsyncSession, seed_artist, seed_channel):
    """Seed a complete post context: artist → release → campaign → post + asset.

    Returns a dict of all created entities for use in tests.
    """
    from amplify.db.models.release import ReleaseModel
    from amplify.db.models.track import TrackModel
    from amplify.db.models.campaign import CampaignModel
    from amplify.db.models.post import PostModel
    from amplify.db.models.asset import AssetModel, AssetVariantModel

    artist = seed_artist
    channel = seed_channel

    # Release
    release = ReleaseModel(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        artist_id=artist.id,
        title="Midnight Signal",
        release_type="single",
        release_date=date(2026, 3, 29),
        upc="198765432100",
    )
    db_session.add(release)

    # Track
    track = TrackModel(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        release_id=release.id,
        title="Midnight Signal",
        track_number=1,
        isrc="USRC12345678",
        duration_seconds=210,
    )
    db_session.add(track)

    # Campaign
    campaign = CampaignModel(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        artist_id=artist.id,
        release_id=release.id,
        name="Midnight Signal Launch",
        status="active",
        phase="pre_release",
        start_date=date(2026, 3, 15),
        end_date=date(2026, 4, 15),
        goals={"presaves": 500, "streams": 10000},
    )
    db_session.add(campaign)

    # Asset
    asset = AssetModel(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        release_id=release.id,
        campaign_id=campaign.id,
        asset_type="video",
        name="teaser_reel.mp4",
        file_url="https://cdn.amplify.com/assets/teaser_reel.mp4",
        file_size_bytes=15_000_000,
        mime_type="video/mp4",
    )
    db_session.add(asset)

    # Asset variant (Instagram-optimized)
    variant = AssetVariantModel(
        id=uuid.uuid4(),
        asset_id=asset.id,
        platform="instagram",
        file_url="https://cdn.amplify.com/assets/teaser_reel_ig.mp4",
        width=1080,
        height=1920,
        duration_seconds=28.5,
        format="mp4",
    )
    db_session.add(variant)

    # Post — published
    post = PostModel(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        campaign_id=campaign.id,
        channel_id=channel.id,
        platform="instagram",
        status="published",
        content_text=(
            "🔥 New music dropping March 29!\n\n"
            "Pre-save 'Midnight Signal' now — link in bio\n\n"
            "#newmusic #indieelectronic #presave @spotify"
        ),
        media_urls=["https://cdn.amplify.com/assets/teaser_reel.mp4"],
        published_at=datetime(2026, 3, 20, 18, 30, 0),
        policy_decision="allow",
    )
    db_session.add(post)

    # Second post — for sequence position testing
    post2 = PostModel(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        campaign_id=campaign.id,
        channel_id=channel.id,
        platform="instagram",
        status="scheduled",
        content_text="Have you heard? The countdown is on! Pre-save now 🎵",
        media_urls=[],
        scheduled_at=datetime(2026, 3, 22, 14, 0, 0),
        policy_decision="require_approval",
    )
    db_session.add(post2)

    await db_session.commit()

    return {
        "artist": artist,
        "channel": channel,
        "release": release,
        "track": track,
        "campaign": campaign,
        "asset": asset,
        "variant": variant,
        "post": post,
        "post2": post2,
    }


# ── Pure extractor unit tests ────────────────────────────────────


class TestTimingExtractor:
    def test_extracts_hour_and_day(self):
        from amplify.learning.feature_extraction import extract_timing_features

        # Thursday March 20, 2026 at 6:30 PM
        ts = datetime(2026, 3, 20, 18, 30, 0)
        f = extract_timing_features(published_at=ts)

        assert f["post_hour"] == 18
        assert f["post_day_of_week"] == 4  # Friday (0=Mon)
        assert f["timing_day_part"] == "evening"
        assert f["timing_is_weekend"] is False

    def test_weekend_detection(self):
        from amplify.learning.feature_extraction import extract_timing_features

        # Saturday
        ts = datetime(2026, 3, 21, 10, 0, 0)
        f = extract_timing_features(published_at=ts)
        assert f["timing_is_weekend"] is True

    def test_falls_back_to_scheduled_at(self):
        from amplify.learning.feature_extraction import extract_timing_features

        ts = datetime(2026, 3, 22, 8, 0, 0)
        f = extract_timing_features(scheduled_at=ts)
        assert f["post_hour"] == 8
        assert f["timing_day_part"] == "morning"

    def test_none_returns_empty(self):
        from amplify.learning.feature_extraction import extract_timing_features

        assert extract_timing_features() == {}

    def test_day_parts(self):
        from amplify.learning.feature_extraction.extractors import _day_part

        assert _day_part(3) == "night"
        assert _day_part(8) == "morning"
        assert _day_part(14) == "afternoon"
        assert _day_part(19) == "evening"
        assert _day_part(22) == "night"


class TestContentExtractor:
    def test_full_caption_analysis(self):
        from amplify.learning.feature_extraction import extract_content_features

        text = (
            "🔥 New music dropping March 29!\n\n"
            "Pre-save 'Midnight Signal' now — link in bio\n\n"
            "#newmusic #indieelectronic #presave @spotify"
        )
        f = extract_content_features(
            content_text=text,
            media_urls=["https://cdn.amplify.com/vid.mp4"],
        )

        assert f["caption_length"] == len(text)
        assert f["caption_length_bucket"] == "short"  # 50-150 chars
        assert f["hashtag_count"] == 3
        assert f["mention_count"] == 1
        assert f["emoji_count"] >= 1
        assert f["has_link"] is False  # "link in bio" is text, not a URL
        assert f["content_type"] == "image"  # single media_url
        assert f["media_count"] == 1
        assert f["cta_type"] == "presave"
        assert f["hook_family"] == "emoji_lead"

    def test_carousel_detection(self):
        from amplify.learning.feature_extraction import extract_content_features

        f = extract_content_features(media_urls=["a.jpg", "b.jpg", "c.jpg"])
        assert f["content_type"] == "carousel"
        assert f["media_count"] == 3

    def test_explicit_media_type_wins(self):
        from amplify.learning.feature_extraction import extract_content_features

        f = extract_content_features(
            media_urls=["a.mp4"],
            media_type="reel",
        )
        assert f["content_type"] == "reel"

    def test_text_only(self):
        from amplify.learning.feature_extraction import extract_content_features

        f = extract_content_features(content_text="Just a text post")
        assert f["content_type"] == "text"
        assert f["media_count"] == 0

    def test_empty_content(self):
        from amplify.learning.feature_extraction import extract_content_features

        f = extract_content_features()
        assert f["caption_length"] == 0
        assert f["caption_length_bucket"] == "empty"

    def test_cta_detection_stream(self):
        from amplify.learning.feature_extraction import extract_content_features

        f = extract_content_features(content_text="Stream now on Spotify!")
        assert f["cta_type"] == "stream"

    def test_cta_detection_watch(self):
        from amplify.learning.feature_extraction import extract_content_features

        f = extract_content_features(content_text="Watch the official video here!")
        assert f["cta_type"] == "watch"

    def test_cta_none_when_absent(self):
        from amplify.learning.feature_extraction import extract_content_features

        f = extract_content_features(content_text="Just vibes")
        assert f["cta_type"] is None

    def test_hook_question(self):
        from amplify.learning.feature_extraction import extract_content_features

        f = extract_content_features(content_text="Have you heard the new track?")
        assert f["hook_family"] == "question"

    def test_hook_announcement(self):
        from amplify.learning.feature_extraction import extract_content_features

        f = extract_content_features(content_text="New single out now!")
        assert f["hook_family"] == "announcement"

    def test_hook_number(self):
        from amplify.learning.feature_extraction import extract_content_features

        f = extract_content_features(content_text="3 days until launch 🚀")
        assert f["hook_family"] == "number"

    def test_hook_direct_address(self):
        from amplify.learning.feature_extraction import extract_content_features

        f = extract_content_features(content_text="You asked for it — here it is!")
        assert f["hook_family"] == "direct_address"

    def test_caption_length_buckets(self):
        from amplify.learning.feature_extraction import extract_content_features

        assert extract_content_features(content_text="")["caption_length_bucket"] == "empty"
        assert extract_content_features(content_text="x" * 30)["caption_length_bucket"] == "micro"
        assert extract_content_features(content_text="x" * 100)["caption_length_bucket"] == "short"
        assert extract_content_features(content_text="x" * 200)["caption_length_bucket"] == "medium"
        assert extract_content_features(content_text="x" * 400)["caption_length_bucket"] == "long"
        assert extract_content_features(content_text="x" * 700)["caption_length_bucket"] == "very_long"


class TestAssetExtractor:
    def test_video_with_dimensions(self):
        from amplify.learning.feature_extraction import extract_asset_features

        f = extract_asset_features(
            asset_type="video",
            width=1080,
            height=1920,
            duration_seconds=28.5,
            file_size_bytes=15_000_000,
        )

        assert f["asset_type"] == "video"
        assert f["asset_aspect_ratio"] == 0.56
        assert f["asset_orientation"] == "portrait"
        assert f["asset_video_duration"] == 28.5
        assert f["asset_video_duration_bucket"] == "medium"
        assert f["asset_file_size_mb"] == 14.31

    def test_landscape_image(self):
        from amplify.learning.feature_extraction import extract_asset_features

        f = extract_asset_features(width=1920, height=1080)
        assert f["asset_aspect_ratio"] == 1.78
        assert f["asset_orientation"] == "landscape"

    def test_square_image(self):
        from amplify.learning.feature_extraction import extract_asset_features

        f = extract_asset_features(width=1080, height=1080)
        assert f["asset_aspect_ratio"] == 1.0
        assert f["asset_orientation"] == "square"

    def test_video_duration_buckets(self):
        from amplify.learning.feature_extraction import extract_asset_features

        assert extract_asset_features(duration_seconds=2)["asset_video_duration_bucket"] == "micro"
        assert extract_asset_features(duration_seconds=10)["asset_video_duration_bucket"] == "short"
        assert extract_asset_features(duration_seconds=25)["asset_video_duration_bucket"] == "medium"
        assert extract_asset_features(duration_seconds=45)["asset_video_duration_bucket"] == "standard"
        assert extract_asset_features(duration_seconds=120)["asset_video_duration_bucket"] == "long"
        assert extract_asset_features(duration_seconds=300)["asset_video_duration_bucket"] == "very_long"

    def test_empty_returns_empty(self):
        from amplify.learning.feature_extraction import extract_asset_features

        assert extract_asset_features() == {}


class TestChannelExtractor:
    def test_full_channel(self):
        from amplify.learning.feature_extraction import extract_channel_features

        f = extract_channel_features(
            platform="instagram",
            integration_mode="automatic",
            account_type="professional",
            capabilities={"can_publish": True},
        )
        assert f["platform"] == "instagram"
        assert f["channel_integration_mode"] == "automatic"
        assert f["channel_account_type"] == "professional"
        assert f["channel_can_publish"] is True


class TestCampaignExtractor:
    def test_release_phase_computation(self):
        from amplify.learning.feature_extraction import extract_campaign_features

        f = extract_campaign_features(
            campaign_phase="pre_release",
            release_date=date(2026, 3, 29),
            publish_date=date(2026, 3, 20),
        )
        assert f["campaign_phase"] == "pre_release"
        assert f["release_phase"] == "pre_release"
        assert f["release_days_offset"] == -9

    def test_release_day(self):
        from amplify.learning.feature_extraction import extract_campaign_features

        f = extract_campaign_features(
            release_date=date(2026, 3, 29),
            publish_date=date(2026, 3, 29),
        )
        assert f["release_phase"] == "release_day"
        assert f["release_days_offset"] == 0

    def test_countdown(self):
        from amplify.learning.feature_extraction import extract_campaign_features

        f = extract_campaign_features(
            release_date=date(2026, 3, 29),
            publish_date=date(2026, 3, 27),
        )
        assert f["release_phase"] == "countdown"

    def test_objective_from_goals(self):
        from amplify.learning.feature_extraction import extract_campaign_features

        f = extract_campaign_features(
            campaign_goals={"presaves": 500, "streams": 10000},
        )
        assert f["campaign_objective"] == "streams"

    def test_explicit_objective_wins(self):
        from amplify.learning.feature_extraction import extract_campaign_features

        f = extract_campaign_features(
            campaign_objective="awareness",
            campaign_goals={"streams": 10000},
        )
        assert f["campaign_objective"] == "awareness"


class TestSequenceExtractor:
    def test_sequence_features(self):
        from amplify.learning.feature_extraction import extract_sequence_features

        f = extract_sequence_features(
            sequence_position=3,
            total_in_campaign=10,
            prior_post_count_24h=1,
        )
        assert f["sequence_position"] == 3
        assert f["campaign_total_posts"] == 10
        assert f["sequence_progress"] == 0.3
        assert f["posts_in_last_24h"] == 1


class TestPolicyExtractor:
    def test_policy_features(self):
        from amplify.learning.feature_extraction import extract_policy_features

        f = extract_policy_features(
            policy_decision="require_approval",
            approval_required=True,
            prompt_version_id="pv-123",
        )
        assert f["policy_decision"] == "require_approval"
        assert f["approval_required"] is True
        assert f["prompt_version_id"] == "pv-123"


class TestEarlyMetricsExtractor:
    def test_early_metrics(self):
        from amplify.learning.feature_extraction import extract_early_metrics_features

        f = extract_early_metrics_features(
            impressions=5000,
            engagements=250,
            reach=3000,
            saves=150,
            measurement_window="24h",
        )
        assert f["metrics_window"] == "24h"
        assert f["early_impressions_log"] == pytest.approx(8.517, abs=0.01)
        assert f["early_engagement_rate"] == pytest.approx(0.05, abs=0.001)
        assert f["early_save_rate"] == pytest.approx(0.05, abs=0.001)

    def test_empty_metrics(self):
        from amplify.learning.feature_extraction import extract_early_metrics_features

        assert extract_early_metrics_features() == {}


class TestCohortExtractor:
    def test_cohort_tags(self):
        from amplify.learning.feature_extraction import extract_cohort_features

        f = extract_cohort_features(cohort_tags=["indie", "electronic", "emerging"])
        assert f["cohort_tags"] == ["electronic", "emerging", "indie"]  # sorted

    def test_empty(self):
        from amplify.learning.feature_extraction import extract_cohort_features

        assert extract_cohort_features() == {}


# ── Pipeline composition tests ───────────────────────────────────


class TestPipeline:
    def test_full_pipeline(self):
        from amplify.learning.feature_extraction.pipeline import (
            AssetContext,
            CampaignContext,
            ChannelContext,
            PolicyContext,
            PostContext,
            ReleaseContext,
            SequenceContext,
            TenantContext,
            extract_features,
        )

        ctx = PostContext(
            content_text=(
                "🔥 New music dropping March 29!\n\n"
                "Pre-save now — link in bio\n\n"
                "#newmusic #presave @spotify"
            ),
            media_urls=["https://cdn.amplify.com/vid.mp4"],
            published_at=datetime(2026, 3, 20, 18, 30, 0),
            asset=AssetContext(
                asset_type="video",
                width=1080,
                height=1920,
                duration_seconds=28.5,
                file_size_bytes=15_000_000,
            ),
            channel=ChannelContext(
                platform="instagram",
                integration_mode="automatic",
                account_type="professional",
            ),
            campaign=CampaignContext(
                phase="pre_release",
                goals={"presaves": 500},
                release_date=date(2026, 3, 29),
                release_type="single",
                track_count=1,
                has_upc=True,
            ),
            release=ReleaseContext(
                genre="electronic",
                track_isrc="USRC12345678",
                track_title="Midnight Signal",
            ),
            tenant=TenantContext(plan_tier="solo", onboarding_completed=True),
            sequence=SequenceContext(position=1, total_in_campaign=8),
            policy=PolicyContext(policy_decision="allow"),
        )

        features = extract_features(ctx)

        # Timing
        assert features["post_hour"] == 18
        assert features["timing_day_part"] == "evening"

        # Content
        assert features["hashtag_count"] == 2
        assert features["cta_type"] == "presave"
        assert features["hook_family"] == "emoji_lead"

        # Asset
        assert features["asset_type"] == "video"
        assert features["asset_orientation"] == "portrait"
        assert features["asset_video_duration_bucket"] == "medium"

        # Channel
        assert features["platform"] == "instagram"
        assert features["channel_integration_mode"] == "automatic"

        # Campaign
        assert features["campaign_phase"] == "pre_release"
        assert features["release_phase"] == "pre_release"
        assert features["release_days_offset"] == -9
        assert features["release_type"] == "single"
        assert features["release_has_upc"] is True

        # Release / track
        assert features["artist_genre"] == "electronic"
        assert features["track_isrc"] == "USRC12345678"

        # Tenant
        assert features["tenant_plan_tier"] == "solo"

        # Sequence
        assert features["sequence_position"] == 1
        assert features["campaign_total_posts"] == 8

        # Policy
        assert features["policy_decision"] == "allow"

        # Schema version stamp
        assert features["_schema_version"] == 1

    def test_minimal_context(self):
        """Pipeline handles empty context gracefully."""
        from amplify.learning.feature_extraction.pipeline import (
            PostContext, extract_features,
        )

        features = extract_features(PostContext())
        assert features["caption_length"] == 0
        assert features["_schema_version"] == 1
        # Timing features absent
        assert "post_hour" not in features

    def test_partial_context(self):
        """Pipeline handles partially populated context."""
        from amplify.learning.feature_extraction.pipeline import (
            ChannelContext, PostContext, extract_features,
        )

        ctx = PostContext(
            content_text="Hello world",
            channel=ChannelContext(platform="tiktok"),
        )
        features = extract_features(ctx)

        assert features["platform"] == "tiktok"
        assert features["caption_length"] == 11
        assert "post_hour" not in features
        assert "asset_type" not in features


# ── Feature schema validation tests ──────────────────────────────


class TestFeatureSchemaValidation:
    def test_registry_has_all_pipeline_features(self):
        """Every feature the pipeline can produce is registered in the schema."""
        from amplify.learning.feature_extraction import FeatureRegistry
        from amplify.learning.feature_extraction.pipeline import (
            AssetContext,
            CampaignContext,
            ChannelContext,
            EarlyMetricsContext,
            PolicyContext,
            PostContext,
            ReleaseContext,
            SequenceContext,
            TenantContext,
            extract_features,
        )
        from amplify.learning.schemas.feature import FeatureVector

        FeatureRegistry.reset()
        schema = FeatureRegistry.schema()

        # Build a maximally-populated context
        ctx = PostContext(
            content_text="🔥 New single out now! Pre-save — link in bio #music @artist",
            media_urls=["a.mp4"],
            media_type="reel",
            published_at=datetime(2026, 3, 20, 18, 30, 0),
            asset=AssetContext(
                asset_type="video", width=1080, height=1920,
                duration_seconds=30, file_size_bytes=10_000_000,
            ),
            channel=ChannelContext(
                platform="instagram", integration_mode="automatic",
                account_type="professional", capabilities={"can_publish": True},
            ),
            campaign=CampaignContext(
                phase="pre_release", objective="presaves",
                goals={"presaves": 500}, release_date=date(2026, 3, 29),
                release_type="single", track_count=1, has_upc=True,
            ),
            release=ReleaseContext(
                genre="electronic", track_isrc="USRC12345678",
                track_title="Test", track_number=1,
            ),
            tenant=TenantContext(plan_tier="solo", onboarding_completed=True),
            sequence=SequenceContext(position=1, total_in_campaign=8, prior_post_count_24h=2),
            policy=PolicyContext(
                policy_decision="allow", approval_required=False,
                prompt_version_id="pv-1",
            ),
            early_metrics=EarlyMetricsContext(
                impressions=5000, engagements=250, reach=3000,
                saves=150, measurement_window="24h",
            ),
            cohort_tags=["indie", "electronic"],
        )

        features = extract_features(ctx)
        vector = FeatureVector(values=features)
        warnings = schema.validate_vector(vector)

        # Only _schema_version should be "unknown" (internal metadata, not a feature)
        unknown = [w for w in warnings if "_schema_version" not in w]
        assert unknown == [], f"Unregistered features found: {unknown}"

    def test_registry_feature_count(self):
        """Sanity check that the registry has a reasonable number of features."""
        from amplify.learning.feature_extraction import FeatureRegistry

        FeatureRegistry.reset()
        schema = FeatureRegistry.schema()
        assert len(schema.features) >= 35


# ── DB integration tests (FeatureExtractionService) ──────────────


class TestFeatureExtractionService:
    @pytest.mark.asyncio
    async def test_extract_and_persist_full_post(
        self, db_session: AsyncSession, seed_full_context,
    ):
        from app.services.feature_extraction_service import FeatureExtractionService

        ctx = seed_full_context
        svc = FeatureExtractionService(db_session, TEST_TENANT_ID)

        vector = await svc.extract_and_persist(ctx["post"].id)

        assert vector.post_id == ctx["post"].id
        assert vector.tenant_id == TEST_TENANT_ID
        assert vector.platform == "instagram"
        assert vector.post_hour == 18
        assert vector.post_day_of_week == 4
        assert vector.caption_length > 0
        assert vector.hashtag_count == 3
        assert vector.has_link is False
        assert vector.schema_version == 1

        # Check features dict
        f = vector.features
        assert f["cta_type"] == "presave"
        assert f["hook_family"] == "emoji_lead"
        assert f["asset_type"] == "video"
        assert f["asset_orientation"] == "portrait"
        assert f["release_phase"] == "pre_release"
        assert f["campaign_phase"] == "pre_release"
        assert f["tenant_plan_tier"] == "solo"
        assert f["policy_decision"] == "allow"
        assert f["sequence_position"] >= 1

    @pytest.mark.asyncio
    async def test_extract_updates_existing_vector(
        self, db_session: AsyncSession, seed_full_context,
    ):
        from app.services.feature_extraction_service import FeatureExtractionService

        ctx = seed_full_context
        svc = FeatureExtractionService(db_session, TEST_TENANT_ID)

        v1 = await svc.extract_and_persist(ctx["post"].id)
        v2 = await svc.extract_and_persist(ctx["post"].id)

        assert v1.id == v2.id  # Same row, updated

    @pytest.mark.asyncio
    async def test_extract_scheduled_post(
        self, db_session: AsyncSession, seed_full_context,
    ):
        from app.services.feature_extraction_service import FeatureExtractionService

        ctx = seed_full_context
        svc = FeatureExtractionService(db_session, TEST_TENANT_ID)

        vector = await svc.extract_and_persist(ctx["post2"].id)

        assert vector.post_hour == 14
        assert vector.features["hook_family"] == "direct_address"  # "Have you heard?" → starts with "Have you"
        assert vector.features["policy_decision"] == "require_approval"
        assert vector.features.get("approval_required") is True

    @pytest.mark.asyncio
    async def test_get_vector(
        self, db_session: AsyncSession, seed_full_context,
    ):
        from app.services.feature_extraction_service import FeatureExtractionService

        ctx = seed_full_context
        svc = FeatureExtractionService(db_session, TEST_TENANT_ID)

        await svc.extract_and_persist(ctx["post"].id)
        vector = await svc.get_vector(ctx["post"].id)

        assert vector is not None
        assert vector.post_id == ctx["post"].id

    @pytest.mark.asyncio
    async def test_get_vector_not_found(
        self, db_session: AsyncSession, seed_tenant,
    ):
        from app.services.feature_extraction_service import FeatureExtractionService

        svc = FeatureExtractionService(db_session, TEST_TENANT_ID)
        assert await svc.get_vector(uuid.uuid4()) is None

    @pytest.mark.asyncio
    async def test_extract_nonexistent_post_raises(
        self, db_session: AsyncSession, seed_tenant,
    ):
        from app.services.feature_extraction_service import FeatureExtractionService

        svc = FeatureExtractionService(db_session, TEST_TENANT_ID)
        with pytest.raises(ValueError, match="not found"):
            await svc.extract_and_persist(uuid.uuid4())

    @pytest.mark.asyncio
    async def test_backfill_finds_posts_without_vectors(
        self, db_session: AsyncSession, seed_full_context,
    ):
        from app.services.feature_extraction_service import FeatureExtractionService

        svc = FeatureExtractionService(db_session, TEST_TENANT_ID)
        result = await svc.backfill()

        # seed_full_context creates 2 posts (published + scheduled)
        assert result["created"] == 2
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_backfill_skips_already_extracted(
        self, db_session: AsyncSession, seed_full_context,
    ):
        from app.services.feature_extraction_service import FeatureExtractionService

        ctx = seed_full_context
        svc = FeatureExtractionService(db_session, TEST_TENANT_ID)

        # Extract one manually
        await svc.extract_and_persist(ctx["post"].id)

        # Backfill should find only the other post
        result = await svc.backfill()
        assert result["created"] == 1

    @pytest.mark.asyncio
    async def test_backfill_explicit_post_ids(
        self, db_session: AsyncSession, seed_full_context,
    ):
        from app.services.feature_extraction_service import FeatureExtractionService

        ctx = seed_full_context
        svc = FeatureExtractionService(db_session, TEST_TENANT_ID)

        result = await svc.backfill(post_ids=[ctx["post"].id])
        assert result["created"] == 1
        assert result["total"] == 1


# ── API endpoint tests ───────────────────────────────────────────


class TestFeatureEndpoints:
    @pytest.mark.asyncio
    async def test_extract_endpoint(self, client, seed_full_context):
        ctx = seed_full_context
        resp = await client.post(f"/api/v1/features/extract/{ctx['post'].id}")
        assert resp.status_code == 201

        data = resp.json()
        assert data["post_id"] == str(ctx["post"].id)
        assert data["platform"] == "instagram"
        assert data["post_hour"] == 18
        assert data["features"]["cta_type"] == "presave"

    @pytest.mark.asyncio
    async def test_get_endpoint(self, client, seed_full_context):
        ctx = seed_full_context
        # Extract first
        await client.post(f"/api/v1/features/extract/{ctx['post'].id}")

        resp = await client.get(f"/api/v1/features/{ctx['post'].id}")
        assert resp.status_code == 200
        assert resp.json()["post_id"] == str(ctx["post"].id)

    @pytest.mark.asyncio
    async def test_get_endpoint_not_found(self, client, seed_tenant):
        fake_id = uuid.uuid4()
        resp = await client.get(f"/api/v1/features/{fake_id}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_extract_not_found(self, client, seed_tenant):
        fake_id = uuid.uuid4()
        resp = await client.post(f"/api/v1/features/extract/{fake_id}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_backfill_endpoint(self, client, seed_full_context):
        resp = await client.post("/api/v1/features/backfill?limit=100")
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] >= 1
        assert data["errors"] == []


# ── Example feature rows for documentation ───────────────────────


class TestExampleFeatureRows:
    """Generate and validate example feature rows.

    These serve as documentation of what a complete feature vector looks like
    for different post scenarios.
    """

    def test_example_instagram_reel_presave(self):
        """Example: Instagram reel promoting a pre-save."""
        from amplify.learning.feature_extraction.pipeline import (
            AssetContext, CampaignContext, ChannelContext, PolicyContext,
            PostContext, ReleaseContext, SequenceContext, TenantContext,
            extract_features,
        )

        ctx = PostContext(
            content_text=(
                "🔥 3 days until Midnight Signal drops!\n\n"
                "Pre-save now — link in bio 🔗\n\n"
                "#newmusic #indieelectronic #presave @spotify @applemusic"
            ),
            media_urls=["https://cdn.amplify.com/reel.mp4"],
            media_type="reel",
            published_at=datetime(2026, 3, 26, 18, 0, 0),
            asset=AssetContext(
                asset_type="video", width=1080, height=1920,
                duration_seconds=15, file_size_bytes=8_000_000,
            ),
            channel=ChannelContext(
                platform="instagram", integration_mode="automatic",
                account_type="professional",
            ),
            campaign=CampaignContext(
                phase="pre_release", goals={"presaves": 500},
                release_date=date(2026, 3, 29),
                release_type="single", track_count=1, has_upc=True,
            ),
            release=ReleaseContext(
                genre="electronic", track_isrc="USRC12345678",
                track_title="Midnight Signal", track_number=1,
            ),
            tenant=TenantContext(plan_tier="solo", onboarding_completed=True),
            sequence=SequenceContext(position=3, total_in_campaign=8),
            policy=PolicyContext(policy_decision="allow"),
        )

        features = extract_features(ctx)

        # Validate the shape of the example row
        assert features["platform"] == "instagram"
        assert features["content_type"] == "reel"
        assert features["release_phase"] == "countdown"
        assert features["asset_video_duration_bucket"] == "short"
        assert features["cta_type"] == "presave"
        assert features["hook_family"] == "emoji_lead"
        assert features["sequence_position"] == 3
        assert features["release_type"] == "single"
        assert features["track_isrc"] == "USRC12345678"
        assert features["artist_genre"] == "electronic"

    def test_example_tiktok_release_day(self):
        """Example: TikTok video on release day."""
        from amplify.learning.feature_extraction.pipeline import (
            AssetContext, CampaignContext, ChannelContext,
            PostContext, ReleaseContext, TenantContext,
            extract_features,
        )

        ctx = PostContext(
            content_text=(
                "OUT NOW 🎵 Stream now on all platforms!\n"
                "#newrelease #indiemusic"
            ),
            media_urls=["https://cdn.amplify.com/tiktok_clip.mp4"],
            media_type="video",
            published_at=datetime(2026, 3, 29, 12, 0, 0),
            asset=AssetContext(
                asset_type="video", width=1080, height=1920,
                duration_seconds=8,
            ),
            channel=ChannelContext(platform="tiktok"),
            campaign=CampaignContext(
                phase="release_week",
                release_date=date(2026, 3, 29),
                release_type="single",
            ),
            release=ReleaseContext(genre="electronic"),
            tenant=TenantContext(plan_tier="solo"),
        )

        features = extract_features(ctx)

        assert features["platform"] == "tiktok"
        assert features["release_phase"] == "release_day"
        assert features["release_days_offset"] == 0
        assert features["cta_type"] == "stream"
        assert features["hook_family"] == "announcement"
        assert features["asset_video_duration_bucket"] == "short"

    def test_example_carousel_evergreen(self):
        """Example: Instagram carousel for evergreen content."""
        from amplify.learning.feature_extraction.pipeline import (
            CampaignContext, ChannelContext, PostContext,
            TenantContext, extract_features,
        )

        ctx = PostContext(
            content_text=(
                "Your top 5 reasons to listen to our latest album 🎧\n\n"
                "Swipe to see them all →\n"
                "#musiclovers #albumreview"
            ),
            media_urls=["slide1.jpg", "slide2.jpg", "slide3.jpg", "slide4.jpg", "slide5.jpg"],
            published_at=datetime(2026, 5, 15, 10, 0, 0),
            channel=ChannelContext(platform="instagram"),
            campaign=CampaignContext(
                phase="evergreen",
                release_date=date(2026, 3, 29),
                release_type="album",
            ),
            tenant=TenantContext(plan_tier="team"),
        )

        features = extract_features(ctx)

        assert features["content_type"] == "carousel"
        assert features["media_count"] == 5
        assert features["release_phase"] == "sustain"
        assert features["hook_family"] == "direct_address"
        assert features["tenant_plan_tier"] == "team"

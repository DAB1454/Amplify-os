"""Tests for core domain model validation and relationships."""

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

import pytest

from amplify.core.domain import (
    Approval,
    ApprovalCreate,
    ApprovalStatus,
    Artist,
    ArtistCreate,
    Asset,
    AssetCreate,
    AssetType,
    AssetVariant,
    AssetVariantCreate,
    Campaign,
    CampaignCreate,
    CampaignPhase,
    CampaignStatus,
    DailyMetric,
    DailyMetricCreate,
    Experiment,
    ExperimentCreate,
    ExperimentStatus,
    MetricEvent,
    MetricEventCreate,
    MetricSource,
    Platform,
    Post,
    PostCreate,
    PostStatus,
    Release,
    ReleaseCreate,
    ReleaseStatus,
    ReleaseType,
    Tenant,
    TenantCreate,
    TenantUpdate,
    Track,
    TrackCreate,
)


# ---------------------------------------------------------------------------
# Tenant
# ---------------------------------------------------------------------------


class TestTenant:
    def test_tenant_create_generates_slug(self):
        tc = TenantCreate(name="My Studio")
        slug = tc.generate_slug()
        assert slug == "my-studio"

    def test_tenant_create_generates_slug_strips_special_chars(self):
        tc = TenantCreate(name="  Hello World!!! ")
        slug = tc.generate_slug()
        assert slug == "hello-world"

    def test_tenant_create_uses_explicit_slug(self):
        tc = TenantCreate(name="My Studio", slug="custom-slug")
        slug = tc.generate_slug()
        assert slug == "custom-slug"

    def test_tenant_default_plan_tier(self):
        tenant = Tenant(name="Test", slug="test")
        assert tenant.plan_tier == "solo"

    def test_tenant_valid_plan_tiers(self):
        for tier in ("solo", "pro", "agency"):
            tenant = Tenant(name="Test", slug="test", plan_tier=tier)
            assert tenant.plan_tier == tier

    def test_tenant_invalid_plan_tier_rejected(self):
        with pytest.raises(Exception):
            Tenant(name="Test", slug="test", plan_tier="enterprise")


# ---------------------------------------------------------------------------
# Artist
# ---------------------------------------------------------------------------


class TestArtist:
    def test_artist_create_minimal(self):
        ac = ArtistCreate(name="Luna Vega")
        assert ac.name == "Luna Vega"
        assert ac.bio == ""
        assert ac.genre == ""

    def test_artist_create_full(self):
        ac = ArtistCreate(
            name="Luna Vega",
            bio="Indie electronic artist from Brooklyn.",
            genre="Indie Electronic",
            image_url="https://example.com/photo.jpg",
            spotify_id="4ZGK4hkNX6pilPZyivAVY7",
            social_links={"instagram": "https://instagram.com/lunavega"},
        )
        assert ac.name == "Luna Vega"
        assert ac.genre == "Indie Electronic"
        assert ac.spotify_id == "4ZGK4hkNX6pilPZyivAVY7"
        assert "instagram" in ac.social_links

    def test_artist_social_links_default_empty(self):
        ac = ArtistCreate(name="Solo Act")
        assert ac.social_links == {}

    def test_artist_domain_has_tenant_id(self):
        tid = uuid4()
        artist = Artist(tenant_id=tid, name="Test", slug="test")
        assert artist.tenant_id == tid


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------


class TestRelease:
    def test_release_default_status_draft(self):
        release = Release(
            tenant_id=uuid4(),
            artist_id=uuid4(),
            title="Test Album",
            release_type=ReleaseType.ALBUM,
        )
        assert release.status == ReleaseStatus.DRAFT

    def test_release_types_valid(self):
        for rt in ReleaseType:
            release = Release(
                tenant_id=uuid4(),
                artist_id=uuid4(),
                title="Test",
                release_type=rt,
            )
            assert release.release_type == rt

    def test_release_type_values(self):
        assert ReleaseType.SINGLE.value == "single"
        assert ReleaseType.EP.value == "ep"
        assert ReleaseType.ALBUM.value == "album"
        assert ReleaseType.COMPILATION.value == "compilation"

    def test_release_with_tracks(self):
        tid = uuid4()
        aid = uuid4()
        release = Release(
            tenant_id=tid,
            artist_id=aid,
            title="Midnight Frequencies",
            release_type=ReleaseType.ALBUM,
            release_date=date(2026, 4, 15),
        )
        tracks = [
            Track(
                tenant_id=tid,
                release_id=release.id,
                title=f"Track {i}",
                track_number=i,
                duration_seconds=200 + i * 10,
            )
            for i in range(1, 9)
        ]
        assert len(tracks) == 8
        for t in tracks:
            assert t.release_id == release.id


# ---------------------------------------------------------------------------
# Track
# ---------------------------------------------------------------------------


class TestTrack:
    def test_track_create_with_required_fields(self):
        rid = uuid4()
        tc = TrackCreate(release_id=rid, title="Signal Lost", track_number=1)
        assert tc.title == "Signal Lost"
        assert tc.track_number == 1
        assert tc.release_id == rid

    def test_track_optional_fields_nullable(self):
        tc = TrackCreate(release_id=uuid4(), title="Test", track_number=1)
        assert tc.duration_seconds is None
        assert tc.isrc is None
        assert tc.audio_url is None
        assert tc.lyrics is None
        assert tc.is_single is False


# ---------------------------------------------------------------------------
# Campaign
# ---------------------------------------------------------------------------


class TestCampaign:
    def test_campaign_default_status_draft(self):
        campaign = Campaign(
            tenant_id=uuid4(),
            artist_id=uuid4(),
            name="Test Campaign",
        )
        assert campaign.status == CampaignStatus.DRAFT

    def test_campaign_phases_valid(self):
        for phase in CampaignPhase:
            campaign = Campaign(
                tenant_id=uuid4(),
                artist_id=uuid4(),
                name="Test",
                phase=phase,
            )
            assert campaign.phase == phase

    def test_campaign_phase_values(self):
        assert CampaignPhase.PRE_RELEASE.value == "pre_release"
        assert CampaignPhase.RELEASE_DAY.value == "release_day"
        assert CampaignPhase.POST_RELEASE.value == "post_release"
        assert CampaignPhase.EVERGREEN.value == "evergreen"

    def test_campaign_create_minimal(self):
        cc = CampaignCreate(
            artist_id=uuid4(),
            name="Launch Campaign",
        )
        assert cc.name == "Launch Campaign"
        assert cc.release_id is None
        assert cc.budget is None

    def test_campaign_with_dates_and_budget(self):
        cc = CampaignCreate(
            artist_id=uuid4(),
            release_id=uuid4(),
            name="Paid Push",
            start_date=date(2026, 3, 15),
            end_date=date(2026, 5, 15),
            budget=2500.00,
            goals={"target_streams": 50000},
        )
        assert cc.budget == 2500.00
        assert cc.start_date == date(2026, 3, 15)
        assert cc.goals["target_streams"] == 50000


# ---------------------------------------------------------------------------
# Post
# ---------------------------------------------------------------------------


class TestPost:
    def test_post_default_status_draft(self):
        post = Post(
            tenant_id=uuid4(),
            channel_id=uuid4(),
            platform=Platform.INSTAGRAM,
        )
        assert post.status == PostStatus.DRAFT

    def test_post_platforms_valid(self):
        for p in Platform:
            post = Post(
                tenant_id=uuid4(),
                channel_id=uuid4(),
                platform=p,
            )
            assert post.platform == p

    def test_post_platform_values(self):
        assert Platform.INSTAGRAM.value == "instagram"
        assert Platform.TIKTOK.value == "tiktok"
        assert Platform.YOUTUBE.value == "youtube"
        assert Platform.BANDCAMP.value == "bandcamp"
        assert Platform.LINKTREE.value == "linktree"
        assert Platform.EMAIL.value == "email"
        assert Platform.OTHER.value == "other"

    def test_post_media_urls_default_empty(self):
        pc = PostCreate(
            channel_id=uuid4(),
            platform=Platform.TIKTOK,
        )
        assert pc.media_urls == []

    def test_post_status_values(self):
        assert PostStatus.DRAFT.value == "draft"
        assert PostStatus.SCHEDULED.value == "scheduled"
        assert PostStatus.PUBLISHING.value == "publishing"
        assert PostStatus.PUBLISHED.value == "published"
        assert PostStatus.FAILED.value == "failed"


# ---------------------------------------------------------------------------
# Asset
# ---------------------------------------------------------------------------


class TestAsset:
    def test_asset_types_valid(self):
        for at in AssetType:
            asset = Asset(
                tenant_id=uuid4(),
                asset_type=at,
                name="test",
                file_url="https://example.com/file",
            )
            assert asset.asset_type == at

    def test_asset_type_values(self):
        assert AssetType.IMAGE.value == "image"
        assert AssetType.VIDEO.value == "video"
        assert AssetType.AUDIO.value == "audio"
        assert AssetType.DOCUMENT.value == "document"

    def test_asset_variant_create(self):
        aid = uuid4()
        avc = AssetVariantCreate(
            asset_id=aid,
            platform=Platform.INSTAGRAM,
            file_url="https://example.com/crop.jpg",
            width=1080,
            height=1080,
            format="jpeg",
        )
        assert avc.asset_id == aid
        assert avc.platform == Platform.INSTAGRAM
        assert avc.width == 1080

    def test_asset_variant_optional_fields(self):
        av = AssetVariant(
            asset_id=uuid4(),
            platform=Platform.TIKTOK,
            file_url="https://example.com/file",
        )
        assert av.width is None
        assert av.height is None
        assert av.duration_seconds is None
        assert av.format is None


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


class TestApproval:
    def test_approval_default_status_pending(self):
        approval = Approval(
            tenant_id=uuid4(),
            requested_by=uuid4(),
            action_type="publish_post",
            entity_type="post",
            entity_id=uuid4(),
        )
        assert approval.status == ApprovalStatus.PENDING

    def test_approval_statuses_valid(self):
        assert ApprovalStatus.PENDING.value == "pending"
        assert ApprovalStatus.APPROVED.value == "approved"
        assert ApprovalStatus.REJECTED.value == "rejected"
        assert ApprovalStatus.REVISION_REQUESTED.value == "revision_requested"

    def test_approval_create_minimal(self):
        ac = ApprovalCreate(
            action_type="publish_post",
            entity_type="post",
            entity_id=uuid4(),
        )
        assert ac.action_type == "publish_post"
        assert ac.notes == ""


# ---------------------------------------------------------------------------
# Experiment
# ---------------------------------------------------------------------------


class TestExperiment:
    def test_experiment_default_status_draft(self):
        exp = Experiment(
            tenant_id=uuid4(),
            campaign_id=uuid4(),
            name="Test Experiment",
            hypothesis="Testing something",
        )
        assert exp.status == ExperimentStatus.DRAFT

    def test_experiment_variants_default_empty(self):
        ec = ExperimentCreate(
            campaign_id=uuid4(),
            name="Test",
            hypothesis="H0",
        )
        assert ec.variants == []

    def test_experiment_status_values(self):
        assert ExperimentStatus.DRAFT.value == "draft"
        assert ExperimentStatus.RUNNING.value == "running"
        assert ExperimentStatus.COMPLETED.value == "completed"
        assert ExperimentStatus.CANCELLED.value == "cancelled"

    def test_experiment_with_variants(self):
        ec = ExperimentCreate(
            campaign_id=uuid4(),
            name="Caption Test",
            hypothesis="Cryptic captions outperform descriptive ones",
            variants=[
                {"id": 0, "label": "Cryptic", "caption": "Coming soon..."},
                {"id": 1, "label": "Descriptive", "caption": "New album out April 15!"},
            ],
            metrics_config={"primary_metric": "engagement_rate"},
        )
        assert len(ec.variants) == 2
        assert ec.metrics_config["primary_metric"] == "engagement_rate"


# ---------------------------------------------------------------------------
# Metric
# ---------------------------------------------------------------------------


class TestMetric:
    def test_metric_event_create(self):
        eid = uuid4()
        mec = MetricEventCreate(
            source=MetricSource.INSTAGRAM,
            event_type="like",
            entity_type="post",
            entity_id=eid,
            value=1.0,
        )
        assert mec.source == MetricSource.INSTAGRAM
        assert mec.event_type == "like"
        assert mec.value == 1.0

    def test_daily_metric_create(self):
        eid = uuid4()
        dmc = DailyMetricCreate(
            source=MetricSource.TIKTOK,
            metric_name="follower_count",
            entity_type="artist",
            entity_id=eid,
            date=date(2026, 3, 17),
            value=8730.0,
            previous_value=8690.0,
        )
        assert dmc.metric_name == "follower_count"
        assert dmc.value == 8730.0
        assert dmc.previous_value == 8690.0

    def test_metric_sources_valid(self):
        assert MetricSource.INSTAGRAM.value == "instagram"
        assert MetricSource.TIKTOK.value == "tiktok"
        assert MetricSource.YOUTUBE.value == "youtube"
        assert MetricSource.BANDCAMP.value == "bandcamp"
        assert MetricSource.EMAIL.value == "email"
        assert MetricSource.WEBSITE.value == "website"
        assert MetricSource.INTERNAL.value == "internal"

    def test_metric_event_domain_model(self):
        me = MetricEvent(
            tenant_id=uuid4(),
            source=MetricSource.YOUTUBE,
            event_type="view",
            entity_type="post",
            entity_id=uuid4(),
            value=150.0,
        )
        assert me.value == 150.0
        assert me.source == MetricSource.YOUTUBE

    def test_daily_metric_domain_model(self):
        dm = DailyMetric(
            tenant_id=uuid4(),
            source=MetricSource.INSTAGRAM,
            metric_name="engagement_rate",
            entity_type="artist",
            entity_id=uuid4(),
            date=date.today(),
            value=3.5,
        )
        assert dm.previous_value is None
        assert dm.value == 3.5

"""Tests for the Instagram, TikTok, and YouTube unified adapters.

All tests use dry_run mode so no real API calls are made.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from amplify.adapters.base import (
    AuthenticationError,
    ConnectionStatus,
    PlatformAdapter,
    TokenExpiredError,
)
from amplify.adapters.instagram.adapter import InstagramAdapter
from amplify.adapters.tiktok.adapter import TikTokAdapter
from amplify.adapters.youtube.adapter import YouTubeAdapter


# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def ig_creds() -> dict:
    return {
        "access_token": "test_ig_token_123",
        "account_id": "17841400000000001",
    }


@pytest.fixture
def tt_creds() -> dict:
    return {
        "access_token": "test_tt_token_456",
        "open_id": "tt_open_id_001",
    }


@pytest.fixture
def yt_creds() -> dict:
    return {
        "access_token": "test_yt_token_789",
        "channel_id": "UCxxxxxxxxxxxxxx",
    }


# ── Protocol conformance ────────────────────────────────────────


class TestProtocolConformance:
    def test_instagram_implements_protocol(self):
        assert isinstance(InstagramAdapter(dry_run=True), PlatformAdapter)

    def test_tiktok_implements_protocol(self):
        assert isinstance(TikTokAdapter(dry_run=True), PlatformAdapter)

    def test_youtube_implements_protocol(self):
        assert isinstance(YouTubeAdapter(dry_run=True), PlatformAdapter)


# ── Instagram Adapter (dry_run) ──────────────────────────────────


class TestInstagramDryRun:
    @pytest.mark.asyncio
    async def test_connect(self, ig_creds):
        adapter = InstagramAdapter(dry_run=True)
        health = await adapter.connect(ig_creds)
        assert health.status == ConnectionStatus.CONNECTED
        assert health.platform == "instagram"
        assert health.display_name == "[dry_run]"

    @pytest.mark.asyncio
    async def test_connect_missing_token(self):
        adapter = InstagramAdapter(dry_run=True)
        health = await adapter.connect({})
        assert health.status == ConnectionStatus.ERROR

    @pytest.mark.asyncio
    async def test_publish(self, ig_creds):
        adapter = InstagramAdapter(dry_run=True)
        await adapter.connect(ig_creds)
        result = await adapter.publish(
            "New track dropping! 🔥",
            media_paths=["https://example.com/image.jpg"],
            media_type="photo",
        )
        assert result.dry_run is True
        assert result.platform == "instagram"
        assert result.status == "dry_run"

    @pytest.mark.asyncio
    async def test_publish_not_connected(self):
        adapter = InstagramAdapter(dry_run=True)
        with pytest.raises(AuthenticationError, match="not connected"):
            await adapter.publish("hello")

    @pytest.mark.asyncio
    async def test_fetch_post(self, ig_creds):
        adapter = InstagramAdapter(dry_run=True)
        await adapter.connect(ig_creds)
        post = await adapter.fetch_post("12345")
        assert post.platform == "instagram"
        assert "[dry_run]" in post.content_text

    @pytest.mark.asyncio
    async def test_fetch_comments(self, ig_creds):
        adapter = InstagramAdapter(dry_run=True)
        await adapter.connect(ig_creds)
        comments = await adapter.fetch_comments("12345")
        assert len(comments) == 1
        assert comments[0].platform == "instagram"

    @pytest.mark.asyncio
    async def test_sync_metrics(self, ig_creds):
        adapter = InstagramAdapter(dry_run=True)
        await adapter.connect(ig_creds)
        metrics = await adapter.sync_metrics("12345")
        assert metrics.platform == "instagram"
        assert metrics.impressions > 0

    @pytest.mark.asyncio
    async def test_expired_token_blocked(self, ig_creds):
        adapter = InstagramAdapter(dry_run=True)
        ig_creds["token_expires_at"] = datetime.utcnow() - timedelta(hours=1)
        await adapter.connect(ig_creds)
        with pytest.raises(TokenExpiredError):
            await adapter.publish("hello", media_paths=["x.jpg"])

    @pytest.mark.asyncio
    async def test_validate_expired(self, ig_creds):
        adapter = InstagramAdapter(dry_run=True)
        ig_creds["token_expires_at"] = datetime.utcnow() - timedelta(hours=1)
        await adapter.connect(ig_creds)
        # Re-validate should show expired
        health = await adapter.validate_connection()
        assert health.status == ConnectionStatus.EXPIRED


# ── TikTok Adapter (dry_run) ─────────────────────────────────────


class TestTikTokDryRun:
    @pytest.mark.asyncio
    async def test_connect(self, tt_creds):
        adapter = TikTokAdapter(dry_run=True)
        health = await adapter.connect(tt_creds)
        assert health.status == ConnectionStatus.CONNECTED
        assert health.platform == "tiktok"

    @pytest.mark.asyncio
    async def test_connect_missing_token(self):
        adapter = TikTokAdapter(dry_run=True)
        health = await adapter.connect({})
        assert health.status == ConnectionStatus.ERROR

    @pytest.mark.asyncio
    async def test_publish(self, tt_creds):
        adapter = TikTokAdapter(dry_run=True)
        await adapter.connect(tt_creds)
        result = await adapter.publish(
            "Check out this new track!",
            media_paths=["/tmp/video.mp4"],
        )
        assert result.dry_run is True
        assert result.platform == "tiktok"

    @pytest.mark.asyncio
    async def test_publish_as_draft(self, tt_creds):
        adapter = TikTokAdapter(dry_run=True)
        await adapter.connect(tt_creds)
        result = await adapter.publish(
            "Draft caption",
            media_paths=["/tmp/video.mp4"],
            as_draft=True,
        )
        assert result.dry_run is True
        assert result.metadata["as_draft"] is True

    @pytest.mark.asyncio
    async def test_fetch_post(self, tt_creds):
        adapter = TikTokAdapter(dry_run=True)
        await adapter.connect(tt_creds)
        post = await adapter.fetch_post("vid_001")
        assert post.platform == "tiktok"

    @pytest.mark.asyncio
    async def test_fetch_comments(self, tt_creds):
        adapter = TikTokAdapter(dry_run=True)
        await adapter.connect(tt_creds)
        comments = await adapter.fetch_comments("vid_001")
        assert len(comments) >= 1

    @pytest.mark.asyncio
    async def test_sync_metrics(self, tt_creds):
        adapter = TikTokAdapter(dry_run=True)
        await adapter.connect(tt_creds)
        metrics = await adapter.sync_metrics("vid_001")
        assert metrics.views > 0

    @pytest.mark.asyncio
    async def test_expired_token_blocked(self, tt_creds):
        adapter = TikTokAdapter(dry_run=True)
        tt_creds["token_expires_at"] = datetime.utcnow() - timedelta(hours=1)
        await adapter.connect(tt_creds)
        with pytest.raises(TokenExpiredError):
            await adapter.publish("hello", media_paths=["x.mp4"])


# ── YouTube Adapter (dry_run) ────────────────────────────────────


class TestYouTubeDryRun:
    @pytest.mark.asyncio
    async def test_connect(self, yt_creds):
        adapter = YouTubeAdapter(dry_run=True)
        health = await adapter.connect(yt_creds)
        assert health.status == ConnectionStatus.CONNECTED
        assert health.platform == "youtube"

    @pytest.mark.asyncio
    async def test_connect_missing_token(self):
        adapter = YouTubeAdapter(dry_run=True)
        health = await adapter.connect({})
        assert health.status == ConnectionStatus.ERROR

    @pytest.mark.asyncio
    async def test_publish(self, yt_creds):
        adapter = YouTubeAdapter(dry_run=True)
        await adapter.connect(yt_creds)
        result = await adapter.publish(
            "For Love of Country - Official Music Video",
            media_paths=["/tmp/music_video.mp4"],
            title="For Love of Country",
            tags=["country", "music"],
            privacyStatus="unlisted",
        )
        assert result.dry_run is True
        assert result.platform == "youtube"

    @pytest.mark.asyncio
    async def test_fetch_post(self, yt_creds):
        adapter = YouTubeAdapter(dry_run=True)
        await adapter.connect(yt_creds)
        post = await adapter.fetch_post("dQw4w9WgXcQ")
        assert post.platform == "youtube"

    @pytest.mark.asyncio
    async def test_fetch_comments(self, yt_creds):
        adapter = YouTubeAdapter(dry_run=True)
        await adapter.connect(yt_creds)
        comments = await adapter.fetch_comments("dQw4w9WgXcQ")
        assert len(comments) >= 1

    @pytest.mark.asyncio
    async def test_sync_metrics(self, yt_creds):
        adapter = YouTubeAdapter(dry_run=True)
        await adapter.connect(yt_creds)
        metrics = await adapter.sync_metrics("dQw4w9WgXcQ")
        assert metrics.views > 0

    @pytest.mark.asyncio
    async def test_expired_token_blocked(self, yt_creds):
        adapter = YouTubeAdapter(dry_run=True)
        yt_creds["token_expires_at"] = datetime.utcnow() - timedelta(hours=1)
        await adapter.connect(yt_creds)
        with pytest.raises(TokenExpiredError):
            await adapter.publish("hello", media_paths=["x.mp4"])

    @pytest.mark.asyncio
    async def test_validate_with_future_token(self, yt_creds):
        adapter = YouTubeAdapter(dry_run=True)
        yt_creds["token_expires_at"] = datetime.utcnow() + timedelta(hours=1)
        await adapter.connect(yt_creds)
        health = await adapter.validate_connection()
        assert health.status == ConnectionStatus.CONNECTED


# ── Auth class validation ────────────────────────────────────────


class TestAuthValidation:
    def test_ig_auth_requires_credentials(self):
        from amplify.adapters.instagram.auth import InstagramAuth
        with pytest.raises(AuthenticationError):
            InstagramAuth(client_id="", client_secret="", redirect_uri="")

    def test_ig_auth_url(self):
        from amplify.adapters.instagram.auth import InstagramAuth
        auth = InstagramAuth(
            client_id="test_id",
            client_secret="test_secret",
            redirect_uri="https://example.com/callback",
        )
        url = auth.get_auth_url()
        assert "test_id" in url
        assert "instagram_content_publish" in url

    def test_tt_auth_requires_credentials(self):
        from amplify.adapters.tiktok.auth import TikTokAuth
        with pytest.raises(AuthenticationError):
            TikTokAuth(client_key="", client_secret="", redirect_uri="")

    def test_tt_auth_url(self):
        from amplify.adapters.tiktok.auth import TikTokAuth
        auth = TikTokAuth(
            client_key="test_key",
            client_secret="test_secret",
            redirect_uri="https://example.com/callback",
        )
        url = auth.get_auth_url()
        assert "test_key" in url
        assert "video.publish" in url

    def test_yt_auth_requires_credentials(self):
        from amplify.adapters.youtube.auth import YouTubeAuth
        with pytest.raises(AuthenticationError):
            YouTubeAuth(client_id="", client_secret="", redirect_uri="")

    def test_yt_auth_url(self):
        from amplify.adapters.youtube.auth import YouTubeAuth
        auth = YouTubeAuth(
            client_id="test_id",
            client_secret="test_secret",
            redirect_uri="https://example.com/callback",
        )
        url = auth.get_auth_url()
        assert "test_id" in url
        assert "youtube" in url
        assert "access_type=offline" in url


# ── Publisher validation ─────────────────────────────────────────


class TestPublisherValidation:
    def test_ig_publisher_requires_token(self):
        from amplify.adapters.instagram.publish import InstagramPublisher
        with pytest.raises(ValueError):
            InstagramPublisher(access_token="", account_id="")

    def test_tt_publisher_requires_token(self):
        from amplify.adapters.tiktok.publish import TikTokPublisher
        with pytest.raises(ValueError):
            TikTokPublisher(access_token="")

    def test_yt_uploader_requires_token(self):
        from amplify.adapters.youtube.upload import YouTubeUploader
        with pytest.raises(ValueError):
            YouTubeUploader(access_token="")

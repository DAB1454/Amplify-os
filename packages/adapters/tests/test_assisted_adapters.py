"""Tests for Bandcamp and Linktree assisted adapters."""

from __future__ import annotations

import pytest

from amplify.adapters.base import ConnectionStatus, PlatformAdapter, PublishError
from amplify.adapters.bandcamp.adapter import BandcampAdapter
from amplify.adapters.linktree.adapter import LinktreeAdapter


# ── Bandcamp Adapter ────────────────────────────────────────────


class TestBandcampProtocol:
    def test_implements_platform_adapter(self):
        assert isinstance(BandcampAdapter(dry_run=True), PlatformAdapter)

    def test_platform_name(self):
        adapter = BandcampAdapter(dry_run=True)
        assert adapter.platform == "bandcamp"


class TestBandcampConnect:
    @pytest.mark.asyncio
    async def test_connect_valid_url(self):
        adapter = BandcampAdapter(dry_run=True)
        health = await adapter.connect({"bandcamp_url": "https://artist.bandcamp.com"})
        assert health.status == ConnectionStatus.CONNECTED
        assert health.platform == "bandcamp"

    @pytest.mark.asyncio
    async def test_connect_with_artist_name(self):
        adapter = BandcampAdapter(dry_run=True)
        health = await adapter.connect({
            "bandcamp_url": "https://drewbaird.bandcamp.com",
            "artist_name": "Drew Baird",
        })
        assert health.status == ConnectionStatus.CONNECTED
        assert health.display_name == "Drew Baird"

    @pytest.mark.asyncio
    async def test_connect_missing_url(self):
        adapter = BandcampAdapter(dry_run=True)
        health = await adapter.connect({})
        assert health.status == ConnectionStatus.ERROR
        assert "required" in health.error

    @pytest.mark.asyncio
    async def test_connect_invalid_url_format(self):
        adapter = BandcampAdapter(dry_run=True)
        health = await adapter.connect({"bandcamp_url": "https://example.com/not-bandcamp"})
        assert health.status == ConnectionStatus.ERROR
        assert "Invalid" in health.error

    @pytest.mark.asyncio
    async def test_connect_with_path(self):
        adapter = BandcampAdapter(dry_run=True)
        health = await adapter.connect({"bandcamp_url": "https://artist.bandcamp.com/album/release"})
        assert health.status == ConnectionStatus.CONNECTED


class TestBandcampValidateUrl:
    @pytest.mark.asyncio
    async def test_validate_valid_bandcamp_url(self):
        adapter = BandcampAdapter(dry_run=True)
        result = await adapter.validate_url("https://artist.bandcamp.com/album/test-album")
        assert result["is_valid"] is True
        assert result["status_code"] == 200

    @pytest.mark.asyncio
    async def test_validate_invalid_pattern(self):
        adapter = BandcampAdapter(dry_run=True)
        result = await adapter.validate_url("https://spotify.com/track/123")
        assert result["is_valid"] is False
        assert "pattern" in result["error"]


class TestBandcampPublishBlocked:
    @pytest.mark.asyncio
    async def test_publish_raises_error(self):
        adapter = BandcampAdapter(dry_run=True)
        await adapter.connect({"bandcamp_url": "https://artist.bandcamp.com"})
        with pytest.raises(PublishError, match="assisted"):
            await adapter.publish("Test content")

    @pytest.mark.asyncio
    async def test_fetch_post_raises_error(self):
        adapter = BandcampAdapter(dry_run=True)
        await adapter.connect({"bandcamp_url": "https://artist.bandcamp.com"})
        with pytest.raises(PublishError):
            await adapter.fetch_post("123")

    @pytest.mark.asyncio
    async def test_fetch_comments_returns_empty(self):
        adapter = BandcampAdapter(dry_run=True)
        await adapter.connect({"bandcamp_url": "https://artist.bandcamp.com"})
        comments = await adapter.fetch_comments("123")
        assert comments == []

    @pytest.mark.asyncio
    async def test_sync_metrics_returns_empty(self):
        adapter = BandcampAdapter(dry_run=True)
        await adapter.connect({"bandcamp_url": "https://artist.bandcamp.com"})
        metrics = await adapter.sync_metrics("123")
        assert metrics.platform == "bandcamp"
        assert metrics.impressions == 0


# ── Linktree Adapter ───────────────────────────────────────────


class TestLinktreeProtocol:
    def test_implements_platform_adapter(self):
        assert isinstance(LinktreeAdapter(dry_run=True), PlatformAdapter)

    def test_platform_name(self):
        adapter = LinktreeAdapter(dry_run=True)
        assert adapter.platform == "linktree"


class TestLinktreeConnect:
    @pytest.mark.asyncio
    async def test_connect_valid_url(self):
        adapter = LinktreeAdapter(dry_run=True)
        health = await adapter.connect({"linktree_url": "https://linktr.ee/drewbaird"})
        assert health.status == ConnectionStatus.CONNECTED
        assert health.platform == "linktree"
        assert health.display_name == "drewbaird"

    @pytest.mark.asyncio
    async def test_connect_missing_url(self):
        adapter = LinktreeAdapter(dry_run=True)
        health = await adapter.connect({})
        assert health.status == ConnectionStatus.ERROR

    @pytest.mark.asyncio
    async def test_connect_invalid_url(self):
        adapter = LinktreeAdapter(dry_run=True)
        health = await adapter.connect({"linktree_url": "https://example.com/not-linktree"})
        assert health.status == ConnectionStatus.ERROR

    @pytest.mark.asyncio
    async def test_connect_extracts_username(self):
        adapter = LinktreeAdapter(dry_run=True)
        await adapter.connect({"linktree_url": "https://linktr.ee/my.artist"})
        assert adapter._username == "my.artist"


class TestLinktreeTrackedLinks:
    def test_generate_tracked_link_basic(self):
        adapter = LinktreeAdapter(dry_run=True)
        link = adapter.generate_tracked_link(
            "https://open.spotify.com/album/123",
            campaign="spring-release",
        )
        assert "utm_source=linktree" in link
        assert "utm_medium=social" in link
        assert "utm_campaign=spring-release" in link

    def test_generate_tracked_link_with_existing_params(self):
        adapter = LinktreeAdapter(dry_run=True)
        link = adapter.generate_tracked_link(
            "https://example.com/page?ref=social",
            campaign="test",
        )
        assert "&utm_source=linktree" in link
        assert "?ref=social" in link

    def test_generate_tracked_link_custom_source(self):
        adapter = LinktreeAdapter(dry_run=True)
        link = adapter.generate_tracked_link(
            "https://example.com",
            source="custom_source",
            medium="email",
            content="header_button",
        )
        assert "utm_source=custom_source" in link
        assert "utm_medium=email" in link
        assert "utm_content=header_button" in link


class TestLinktreeVerifyLinks:
    @pytest.mark.asyncio
    async def test_verify_links_dry_run(self):
        adapter = LinktreeAdapter(dry_run=True)
        results = await adapter.verify_links([
            "https://example.com/a",
            "https://example.com/b",
        ])
        assert len(results) == 2
        assert all(r["is_valid"] for r in results)
        assert all(r["status_code"] == 200 for r in results)


class TestLinktreePublishBlocked:
    @pytest.mark.asyncio
    async def test_publish_raises_error(self):
        adapter = LinktreeAdapter(dry_run=True)
        await adapter.connect({"linktree_url": "https://linktr.ee/test"})
        with pytest.raises(PublishError, match="assisted"):
            await adapter.publish("Test content")

    @pytest.mark.asyncio
    async def test_fetch_comments_returns_empty(self):
        adapter = LinktreeAdapter(dry_run=True)
        comments = await adapter.fetch_comments("123")
        assert comments == []

    @pytest.mark.asyncio
    async def test_sync_metrics_returns_empty(self):
        adapter = LinktreeAdapter(dry_run=True)
        metrics = await adapter.sync_metrics("123")
        assert metrics.platform == "linktree"

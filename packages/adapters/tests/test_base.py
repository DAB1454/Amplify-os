"""Tests for the adapter protocol, base class, error types, and data models."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest

from amplify.adapters.base import (
    AdapterError,
    AuthenticationError,
    BaseAdapter,
    Comment,
    ConnectionHealth,
    ConnectionStatus,
    FetchedPost,
    FetchError,
    MetricSnapshot,
    PlatformAdapter,
    PublishError,
    PublishResult,
    RateLimitError,
    TokenExpiredError,
    TokenRefreshError,
    ValidationError,
)


# ── Error hierarchy ──────────────────────────────────────────────


class TestErrors:
    def test_adapter_error_fields(self):
        e = AdapterError("boom", platform="instagram", details={"code": 400})
        assert str(e) == "boom"
        assert e.platform == "instagram"
        assert e.details == {"code": 400}

    def test_authentication_error_inherits(self):
        e = AuthenticationError("bad token", platform="tiktok")
        assert isinstance(e, AdapterError)
        assert e.platform == "tiktok"

    def test_token_expired_inherits(self):
        e = TokenExpiredError("expired")
        assert isinstance(e, AuthenticationError)

    def test_token_refresh_inherits(self):
        e = TokenRefreshError("revoked")
        assert isinstance(e, AuthenticationError)

    def test_rate_limit_retry_after(self):
        e = RateLimitError("slow down", platform="youtube", retry_after=120)
        assert e.retry_after == 120
        assert isinstance(e, AdapterError)

    def test_publish_error(self):
        e = PublishError("failed", platform="instagram")
        assert isinstance(e, AdapterError)

    def test_fetch_error(self):
        e = FetchError("not found", platform="tiktok")
        assert isinstance(e, AdapterError)

    def test_validation_error(self):
        e = ValidationError("missing field")
        assert isinstance(e, AdapterError)


# ── Data models ──────────────────────────────────────────────────


class TestConnectionHealth:
    def test_defaults(self):
        h = ConnectionHealth(platform="instagram", status=ConnectionStatus.CONNECTED)
        assert h.platform == "instagram"
        assert h.status == ConnectionStatus.CONNECTED
        assert h.error is None
        assert h.scopes == []

    def test_all_statuses(self):
        statuses = {s.value for s in ConnectionStatus}
        assert statuses == {"connected", "expired", "revoked", "error", "unknown"}


class TestPublishResult:
    def test_defaults(self):
        r = PublishResult(platform="tiktok")
        assert r.platform_post_id == ""
        assert r.dry_run is False
        assert r.status == "published"

    def test_dry_run_flag(self):
        r = PublishResult(platform="youtube", dry_run=True, status="dry_run")
        assert r.dry_run is True

    def test_json_roundtrip(self):
        r = PublishResult(
            platform="instagram",
            platform_post_id="12345",
            url="https://instagram.com/p/12345/",
            metadata={"media_type": "reel"},
        )
        data = r.model_dump()
        restored = PublishResult(**data)
        assert restored.platform_post_id == "12345"


class TestFetchedPost:
    def test_defaults(self):
        p = FetchedPost(platform="youtube", platform_post_id="abc")
        assert p.content_text == ""
        assert p.media_urls == []
        assert p.engagement == {}


class TestComment:
    def test_defaults(self):
        c = Comment(platform="instagram", comment_id="c1")
        assert c.author == ""
        assert c.is_reply is False
        assert c.like_count == 0


class TestMetricSnapshot:
    def test_defaults(self):
        m = MetricSnapshot(platform="tiktok")
        assert m.impressions == 0
        assert m.views == 0
        assert m.extra == {}


# ── Protocol conformance ────────────────────────────────────────


class _DummyAdapter(BaseAdapter):
    platform = "dummy"

    async def connect(self, credentials: dict[str, Any]) -> ConnectionHealth:
        self._access_token = credentials.get("token", "")
        self._connected = True
        return ConnectionHealth(platform=self.platform, status=ConnectionStatus.CONNECTED)

    async def validate_connection(self) -> ConnectionHealth:
        return ConnectionHealth(platform=self.platform, status=ConnectionStatus.CONNECTED)

    async def publish(self, content: str, media_paths: list[str] | None = None, **kw: Any) -> PublishResult:
        self._require_connected()
        self._check_token_expiry()
        if self.dry_run:
            return self._dry_result("publish", content=content)
        return PublishResult(platform=self.platform, platform_post_id="p1")

    async def fetch_post(self, platform_post_id: str) -> FetchedPost:
        return FetchedPost(platform=self.platform, platform_post_id=platform_post_id)

    async def fetch_comments(self, platform_post_id: str, limit: int = 50) -> list[Comment]:
        return []

    async def sync_metrics(self, platform_post_id: str) -> MetricSnapshot:
        return MetricSnapshot(platform=self.platform, post_id=platform_post_id)


class TestBaseAdapter:
    def test_implements_protocol(self):
        adapter = _DummyAdapter()
        assert isinstance(adapter, PlatformAdapter)

    def test_dry_run_default_false(self):
        adapter = _DummyAdapter()
        assert adapter.dry_run is False

    def test_dry_run_flag(self):
        adapter = _DummyAdapter(dry_run=True)
        assert adapter.dry_run is True

    @pytest.mark.asyncio
    async def test_connect_sets_connected(self):
        adapter = _DummyAdapter()
        health = await adapter.connect({"token": "abc"})
        assert health.status == ConnectionStatus.CONNECTED

    @pytest.mark.asyncio
    async def test_require_connected_raises(self):
        adapter = _DummyAdapter()
        with pytest.raises(AuthenticationError, match="not connected"):
            await adapter.publish("hello")

    def test_require_token_raises_when_empty(self):
        adapter = _DummyAdapter()
        adapter._connected = True
        adapter._access_token = ""
        with pytest.raises(AuthenticationError, match="No access token"):
            adapter._require_token()

    @pytest.mark.asyncio
    async def test_token_expiry_check(self):
        adapter = _DummyAdapter()
        await adapter.connect({"token": "abc"})
        adapter._token_expires_at = datetime.utcnow() - timedelta(hours=1)
        with pytest.raises(TokenExpiredError):
            await adapter.publish("hello")

    @pytest.mark.asyncio
    async def test_dry_run_publish(self):
        adapter = _DummyAdapter(dry_run=True)
        await adapter.connect({"token": "abc"})
        result = await adapter.publish("test content")
        assert result.dry_run is True
        assert result.status == "dry_run"
        assert result.platform == "dummy"

    def test_is_token_expired_none(self):
        adapter = _DummyAdapter()
        assert adapter._is_token_expired() is False

    def test_is_token_expired_future(self):
        adapter = _DummyAdapter()
        adapter._token_expires_at = datetime.utcnow() + timedelta(hours=1)
        assert adapter._is_token_expired() is False

    def test_is_token_expired_past(self):
        adapter = _DummyAdapter()
        adapter._token_expires_at = datetime.utcnow() - timedelta(hours=1)
        assert adapter._is_token_expired() is True

"""Tests for OAuth flow, PKCE, and connection lifecycle."""

import json
import os
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# Env must be set before app imports
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEPLOYMENT_MODE", "local")
os.environ.setdefault("REDIS_URL", "")

from amplify.adapters.token_store import TokenSet

TEST_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
TEST_ARTIST_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")


class FakeRedis:
    """In-memory Redis stand-in for tests."""

    def __init__(self):
        self._store: dict[str, str] = {}

    async def setex(self, key: str, ttl: int, value: str):
        self._store[key] = value

    async def get(self, key: str):
        return self._store.get(key)

    async def delete(self, key: str):
        self._store.pop(key, None)


def _make_settings(**overrides):
    from app.config import Settings
    defaults = {
        "deployment_mode": "local",
        "token_encryption_key": "",
        "token_encryption_old_keys": [],
        "instagram_client_id": "ig-id",
        "instagram_client_secret": "ig-secret",
        "instagram_redirect_uri": "http://localhost:8000/oauth/callback/instagram",
        "youtube_client_id": "yt-id",
        "youtube_client_secret": "yt-secret",
        "youtube_redirect_uri": "http://localhost:8000/oauth/callback/youtube",
        "tiktok_client_key": "tt-key",
        "tiktok_client_secret": "tt-secret",
        "tiktok_redirect_uri": "http://localhost:8000/oauth/callback/tiktok",
        "frontend_url": "http://localhost:3000",
    }
    defaults.update(overrides)

    s = Settings()
    for k, v in defaults.items():
        object.__setattr__(s, k, v)
    return s


class TestOAuthService:
    """Unit tests for OAuthService with mocked dependencies."""

    @pytest.mark.asyncio
    async def test_connect_url_returns_auth_url(self):
        from app.services.oauth_service import OAuthService

        redis = FakeRedis()
        settings = _make_settings()
        db = AsyncMock()

        svc = OAuthService(db, redis, settings)
        url = await svc.generate_connect_url("instagram", TEST_TENANT_ID, TEST_ARTIST_ID)

        assert "api.instagram.com/oauth/authorize" in url
        assert "state=" in url

        # Verify state was stored in Redis
        assert len(redis._store) == 1
        state_key = list(redis._store.keys())[0]
        assert state_key.startswith("oauth:state:")

    @pytest.mark.asyncio
    async def test_connect_url_youtube_has_pkce(self):
        from app.services.oauth_service import OAuthService

        redis = FakeRedis()
        settings = _make_settings()
        db = AsyncMock()

        svc = OAuthService(db, redis, settings)
        url = await svc.generate_connect_url("youtube", TEST_TENANT_ID, TEST_ARTIST_ID)

        assert "accounts.google.com" in url
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url

    @pytest.mark.asyncio
    async def test_connect_url_tiktok_has_pkce(self):
        from app.services.oauth_service import OAuthService

        redis = FakeRedis()
        settings = _make_settings()
        db = AsyncMock()

        svc = OAuthService(db, redis, settings)
        url = await svc.generate_connect_url("tiktok", TEST_TENANT_ID, TEST_ARTIST_ID)

        assert "tiktok.com" in url
        assert "code_challenge=" in url

    @pytest.mark.asyncio
    async def test_callback_invalid_state(self):
        from app.services.oauth_service import OAuthService

        redis = FakeRedis()
        settings = _make_settings()
        db = AsyncMock()

        svc = OAuthService(db, redis, settings)

        with pytest.raises(ValueError, match="Invalid or expired"):
            await svc.handle_callback("instagram", "some-code", "bogus-state")

    @pytest.mark.asyncio
    async def test_callback_expired_state(self):
        """State not in Redis → error."""
        from app.services.oauth_service import OAuthService

        redis = FakeRedis()
        settings = _make_settings()
        db = AsyncMock()

        svc = OAuthService(db, redis, settings)

        with pytest.raises(ValueError, match="Invalid or expired"):
            await svc.handle_callback("youtube", "code", "not-in-redis")

    @pytest.mark.asyncio
    async def test_callback_state_replay_attack(self):
        """State consumed, second attempt fails."""
        from app.services.oauth_service import OAuthService

        redis = FakeRedis()
        settings = _make_settings()
        db = AsyncMock()

        svc = OAuthService(db, redis, settings)

        # Generate a URL to create a valid state
        url = await svc.generate_connect_url("instagram", TEST_TENANT_ID, TEST_ARTIST_ID)
        state_key = list(redis._store.keys())[0]
        state = state_key.replace("oauth:state:", "")

        # First consumption — set up mock for exchange
        with patch.object(svc, "_get_auth_class") as mock_auth:
            mock_ig = AsyncMock()
            mock_ig.exchange_code = AsyncMock(return_value=TokenSet(
                access_token="tok", platform="instagram", account_id="123"
            ))
            mock_ig.exchange_long_lived = AsyncMock(return_value=TokenSet(
                access_token="long-tok", platform="instagram", account_id="123"
            ))
            mock_auth.return_value = mock_ig

            # Mock DB for upsert
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            db.execute.return_value = mock_result
            db.flush = AsyncMock()

            await svc.handle_callback("instagram", "code", state)

        # Second attempt with same state — should fail
        with pytest.raises(ValueError, match="Invalid or expired"):
            await svc.handle_callback("instagram", "code", state)

    @pytest.mark.asyncio
    async def test_callback_success_creates_channel(self):
        from app.services.oauth_service import OAuthService

        redis = FakeRedis()
        settings = _make_settings()
        db = AsyncMock()

        svc = OAuthService(db, redis, settings)

        # Generate state
        url = await svc.generate_connect_url("instagram", TEST_TENANT_ID, TEST_ARTIST_ID)
        state_key = list(redis._store.keys())[0]
        state = state_key.replace("oauth:state:", "")

        with patch.object(svc, "_get_auth_class") as mock_auth:
            mock_ig = AsyncMock()
            mock_ig.exchange_code = AsyncMock(return_value=TokenSet(
                access_token="short-tok",
                platform="instagram",
                account_id="ig-user-123",
                scopes=["instagram_basic", "instagram_content_publish"],
            ))
            mock_ig.exchange_long_lived = AsyncMock(return_value=TokenSet(
                access_token="long-tok",
                platform="instagram",
                account_id="ig-user-123",
                scopes=["instagram_basic", "instagram_content_publish"],
            ))
            mock_auth.return_value = mock_ig

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            db.execute.return_value = mock_result
            db.flush = AsyncMock()

            channel = await svc.handle_callback("instagram", "auth-code", state)

        assert channel.platform == "instagram"
        assert channel.is_active is True
        assert channel.platform_account_id == "ig-user-123"
        db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_callback_subset_scopes_granted(self):
        """User grants partial scopes — capabilities reflect correctly."""
        from app.services.oauth_service import OAuthService

        redis = FakeRedis()
        settings = _make_settings()
        db = AsyncMock()

        svc = OAuthService(db, redis, settings)

        url = await svc.generate_connect_url("instagram", TEST_TENANT_ID, TEST_ARTIST_ID)
        state_key = list(redis._store.keys())[0]
        state = state_key.replace("oauth:state:", "")

        with patch.object(svc, "_get_auth_class") as mock_auth:
            mock_ig = AsyncMock()
            # Only basic scope granted — no publish, no insights
            mock_ig.exchange_code = AsyncMock(return_value=TokenSet(
                access_token="tok",
                platform="instagram",
                account_id="123",
                scopes=["instagram_basic"],
            ))
            mock_ig.exchange_long_lived = AsyncMock(return_value=TokenSet(
                access_token="long-tok",
                platform="instagram",
                account_id="123",
                scopes=["instagram_basic"],
            ))
            mock_auth.return_value = mock_ig

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            db.execute.return_value = mock_result
            db.flush = AsyncMock()

            channel = await svc.handle_callback("instagram", "code", state)

        assert channel.capabilities["can_publish"] is False
        assert channel.capabilities["can_sync_metrics"] is False

    @pytest.mark.asyncio
    async def test_refresh_preserves_existing_refresh_token(self):
        from app.services.oauth_service import OAuthService

        redis = FakeRedis()
        settings = _make_settings()
        db = AsyncMock()

        svc = OAuthService(db, redis, settings)

        # Mock channel with existing refresh token
        mock_channel = MagicMock()
        mock_channel.platform = "youtube"
        mock_channel.access_token = "old-access"
        mock_channel.refresh_token = "existing-refresh"
        mock_channel.token_expires_at = datetime.utcnow() + timedelta(hours=1)
        mock_channel.platform_account_id = "yt-123"
        mock_channel.settings = {}
        mock_channel.token_encrypted = False
        mock_channel.granted_scopes = []
        mock_channel.last_refresh_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_channel
        db.execute.return_value = mock_result
        db.flush = AsyncMock()

        with patch.object(svc, "_get_auth_class") as mock_auth:
            mock_yt = AsyncMock()
            # YouTube refresh doesn't return new refresh token
            mock_yt.refresh_token = AsyncMock(return_value=TokenSet(
                access_token="new-access",
                refresh_token="existing-refresh",  # YouTube passes through
                platform="youtube",
                expires_at=datetime.utcnow() + timedelta(hours=1),
            ))
            mock_auth.return_value = mock_yt

            tokens = await svc.refresh_channel_token(uuid.uuid4(), TEST_TENANT_ID)

        assert tokens.refresh_token == "existing-refresh"

    @pytest.mark.asyncio
    async def test_health_check_connected(self):
        from app.services.oauth_service import OAuthService

        redis = FakeRedis()
        settings = _make_settings()
        db = AsyncMock()

        svc = OAuthService(db, redis, settings)

        mock_channel = MagicMock()
        mock_channel.platform = "instagram"
        mock_channel.access_token = "valid-token"
        mock_channel.refresh_token = ""
        mock_channel.token_expires_at = datetime.utcnow() + timedelta(hours=1)
        mock_channel.platform_account_id = "ig-123"
        mock_channel.settings = {}
        mock_channel.token_encrypted = False
        mock_channel.granted_scopes = [
            "instagram_basic", "instagram_content_publish",
            "instagram_manage_comments", "instagram_manage_insights",
            "pages_show_list", "pages_read_engagement",
        ]
        mock_channel.last_refresh_at = None
        mock_channel.last_health_check_at = None
        mock_channel.last_health_status = None
        mock_channel.capabilities = {}

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_channel
        db.execute.return_value = mock_result
        db.flush = AsyncMock()

        health = await svc.check_health(uuid.uuid4(), TEST_TENANT_ID)

        assert health["token_status"] == "valid"
        assert health["scopes_status"] == "complete"
        assert health["publish_capable"] is True

    @pytest.mark.asyncio
    async def test_health_check_expired(self):
        from app.services.oauth_service import OAuthService

        redis = FakeRedis()
        settings = _make_settings()
        db = AsyncMock()

        svc = OAuthService(db, redis, settings)

        mock_channel = MagicMock()
        mock_channel.platform = "youtube"
        mock_channel.access_token = "expired-token"
        mock_channel.refresh_token = "has-refresh"
        mock_channel.token_expires_at = datetime.utcnow() - timedelta(hours=1)
        mock_channel.platform_account_id = "yt-123"
        mock_channel.settings = {}
        mock_channel.token_encrypted = False
        mock_channel.granted_scopes = []
        mock_channel.last_refresh_at = None
        mock_channel.last_health_check_at = None
        mock_channel.last_health_status = None
        mock_channel.capabilities = {}

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_channel
        db.execute.return_value = mock_result
        db.flush = AsyncMock()

        health = await svc.check_health(uuid.uuid4(), TEST_TENANT_ID)

        assert health["token_status"] == "expired"
        assert health["needs_reconnect"] is False  # has refresh token

    @pytest.mark.asyncio
    async def test_disconnect_clears_tokens(self):
        from app.services.oauth_service import OAuthService

        redis = FakeRedis()
        settings = _make_settings()
        db = AsyncMock()

        svc = OAuthService(db, redis, settings)

        mock_channel = MagicMock()
        mock_channel.platform = "instagram"
        mock_channel.access_token = "tok"
        mock_channel.refresh_token = ""
        mock_channel.token_expires_at = None
        mock_channel.platform_account_id = "123"
        mock_channel.settings = {}
        mock_channel.token_encrypted = False
        mock_channel.granted_scopes = ["instagram_basic"]
        mock_channel.is_active = True

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_channel
        db.execute.return_value = mock_result
        db.flush = AsyncMock()

        await svc.disconnect_channel(uuid.uuid4(), TEST_TENANT_ID)

        assert mock_channel.is_active is False
        assert mock_channel.access_token is None
        assert mock_channel.disconnect_reason == "user_requested"

    @pytest.mark.asyncio
    async def test_two_artists_same_platform(self):
        """Two artists on same tenant can have separate connections."""
        from app.services.oauth_service import OAuthService

        redis = FakeRedis()
        settings = _make_settings()
        db = AsyncMock()

        svc = OAuthService(db, redis, settings)

        artist1 = uuid.uuid4()
        artist2 = uuid.uuid4()

        # Generate URLs for both
        url1 = await svc.generate_connect_url("instagram", TEST_TENANT_ID, artist1)
        url2 = await svc.generate_connect_url("instagram", TEST_TENANT_ID, artist2)

        # Both should have different states
        assert len(redis._store) == 2
        assert url1 != url2

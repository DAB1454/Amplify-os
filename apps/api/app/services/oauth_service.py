"""OAuth flow service — PKCE + state management, token exchange, health checks."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import uuid
from base64 import urlsafe_b64encode
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.adapters.base import ConnectionHealth, ConnectionStatus, TokenRefreshError
from amplify.adapters.crypto import TokenEncryptor
from amplify.adapters.token_store import DatabaseTokenStore, TokenSet
from amplify.db.models.channel import ChannelConnectionModel

logger = logging.getLogger(__name__)

# Scopes required for full functionality per platform
REQUIRED_SCOPES: dict[str, list[str]] = {
    "instagram": [
        "instagram_business_basic",
        "instagram_business_content_publish",
        "instagram_business_manage_comments",
        "instagram_business_manage_messages",
    ],
    "youtube": [
        "https://www.googleapis.com/auth/youtube",
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.force-ssl",
    ],
    "tiktok": [
        "user.info.basic",
        "video.publish",
        "video.upload",
    ],
    "twitter": [
        "tweet.read",
        "tweet.write",
        "users.read",
        "offline.access",
        "media.upload",
    ],
}

SCOPE_CAPABILITIES: dict[str, dict[str, list[str]]] = {
    "instagram": {
        "can_publish": ["instagram_business_content_publish"],
        "can_fetch_comments": ["instagram_business_manage_comments"],
        "can_sync_metrics": ["instagram_business_basic"],
    },
    "youtube": {
        "can_publish": ["https://www.googleapis.com/auth/youtube.upload"],
        "can_fetch_comments": ["https://www.googleapis.com/auth/youtube.force-ssl"],
        "can_sync_metrics": ["https://www.googleapis.com/auth/youtube"],
    },
    "tiktok": {
        "can_publish": ["video.publish", "video.upload"],
        "can_fetch_comments": [],
        "can_sync_metrics": [],
    },
    "twitter": {
        "can_publish": ["tweet.write"],
        "can_fetch_comments": ["tweet.read"],
        "can_sync_metrics": ["tweet.read"],
    },
}


class OAuthService:
    """Manages OAuth flows, token lifecycle, and connection health."""

    def __init__(
        self,
        db: AsyncSession,
        redis: Any,
        settings: Any,
    ) -> None:
        self.db = db
        self.redis = redis
        self.settings = settings
        self._encryptor = TokenEncryptor(
            primary_key=settings.token_encryption_key,
            old_keys=settings.token_encryption_old_keys,
            deployment_mode=settings.deployment_mode,
        )
        self._token_store = DatabaseTokenStore(db, self._encryptor)

    # ── PKCE + State ─────────────────────────────────────────────

    @staticmethod
    def _generate_pkce() -> tuple[str, str]:
        """Generate PKCE code_verifier and code_challenge (S256)."""
        verifier = secrets.token_urlsafe(64)[:128]
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = urlsafe_b64encode(digest).rstrip(b"=").decode()
        return verifier, challenge

    async def _begin_pkce_state(
        self,
        platform: str,
        tenant_id: uuid.UUID,
        artist_id: uuid.UUID,
    ) -> tuple[str, str, str]:
        """Create PKCE verifier + signed state, store in Redis.

        Returns (state, code_verifier, code_challenge).
        """
        verifier, challenge = self._generate_pkce()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(16)

        payload = json.dumps({
            "tenant_id": str(tenant_id),
            "artist_id": str(artist_id),
            "platform": platform,
            "nonce": nonce,
            "code_verifier": verifier,
        })

        if self.redis:
            await self.redis.setex(f"oauth:state:{state}", 600, payload)
        else:
            logger.warning("No Redis — storing OAuth state in memory (dev only)")

        return state, verifier, challenge

    async def _consume_pkce_state(self, state: str) -> dict:
        """Look up and delete state from Redis (one-time use).

        Returns the stored payload dict.
        Raises ValueError if state is missing/expired.
        """
        if not self.redis:
            raise ValueError("OAuth state validation requires Redis")

        key = f"oauth:state:{state}"
        raw = await self.redis.get(key)
        if raw is None:
            raise ValueError("Invalid or expired OAuth state (CSRF protection)")

        # Delete immediately — one-time use
        await self.redis.delete(key)
        return json.loads(raw)

    # ── Auth class factories ─────────────────────────────────────

    def _get_instagram_auth(self):
        from amplify.adapters.instagram.auth import InstagramAuth
        return InstagramAuth(
            client_id=self.settings.instagram_client_id,
            client_secret=self.settings.instagram_client_secret,
            redirect_uri=self.settings.instagram_redirect_uri,
        )

    def _get_youtube_auth(self):
        from amplify.adapters.youtube.auth import YouTubeAuth
        return YouTubeAuth(
            client_id=self.settings.youtube_client_id,
            client_secret=self.settings.youtube_client_secret,
            redirect_uri=self.settings.youtube_redirect_uri,
        )

    def _get_tiktok_auth(self):
        from amplify.adapters.tiktok.auth import TikTokAuth
        return TikTokAuth(
            client_key=self.settings.tiktok_client_key,
            client_secret=self.settings.tiktok_client_secret,
            redirect_uri=self.settings.tiktok_redirect_uri,
        )

    def _get_twitter_auth(self):
        from amplify.adapters.twitter.auth import TwitterAuth
        return TwitterAuth(
            client_id=self.settings.twitter_client_id,
            client_secret=self.settings.twitter_client_secret,
            redirect_uri=self.settings.twitter_redirect_uri,
        )

    def _get_auth_class(self, platform: str):
        factories = {
            "instagram": self._get_instagram_auth,
            "youtube": self._get_youtube_auth,
            "tiktok": self._get_tiktok_auth,
            "twitter": self._get_twitter_auth,
        }
        factory = factories.get(platform)
        if not factory:
            raise ValueError(f"Unsupported OAuth platform: {platform}")
        return factory()

    # ── Core methods ─────────────────────────────────────────────

    async def generate_connect_url(
        self,
        platform: str,
        tenant_id: uuid.UUID,
        artist_id: uuid.UUID,
    ) -> str:
        """Generate an OAuth authorization URL with PKCE + state."""
        state, verifier, challenge = await self._begin_pkce_state(
            platform, tenant_id, artist_id
        )
        auth = self._get_auth_class(platform)

        if platform == "instagram":
            # Instagram doesn't use PKCE
            return auth.get_auth_url(state=state)
        else:
            # YouTube, TikTok, Twitter all use PKCE
            return auth.get_auth_url(
                code_challenge=challenge,
                code_challenge_method="S256",
                state=state,
            )

    async def handle_callback(
        self,
        platform: str,
        code: str,
        state: str,
    ) -> ChannelConnectionModel:
        """Exchange auth code for tokens, persist encrypted to DB."""
        # Validate state + extract context
        ctx = await self._consume_pkce_state(state)
        if ctx["platform"] != platform:
            raise ValueError("Platform mismatch in OAuth callback")

        tenant_id = uuid.UUID(ctx["tenant_id"])
        artist_id = uuid.UUID(ctx["artist_id"])
        verifier = ctx["code_verifier"]

        auth = self._get_auth_class(platform)

        # Exchange code with PKCE verifier
        tokens: TokenSet = await auth.exchange_code(code, code_verifier=verifier)
        logger.info("OAuth %s token exchange — extra: %s", platform, tokens.extra)

        # Instagram: upgrade to long-lived token (preserve scopes, account_id, extra)
        if platform == "instagram" and tokens.access_token:
            short_lived_scopes = tokens.scopes
            short_lived_account_id = tokens.account_id
            short_lived_extra = tokens.extra
            tokens = await auth.exchange_long_lived(tokens.access_token)
            tokens.scopes = short_lived_scopes
            tokens.account_id = short_lived_account_id or tokens.account_id or ""
            tokens.extra = short_lived_extra

        # Compute capabilities from granted scopes
        capabilities = self._compute_capabilities(platform, tokens.scopes)

        # Upsert channel connection
        channel = await self._upsert_channel(
            tenant_id=tenant_id,
            artist_id=artist_id,
            platform=platform,
            tokens=tokens,
            capabilities=capabilities,
        )

        return channel

    async def refresh_channel_token(
        self,
        channel_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> TokenSet:
        """Refresh tokens for a channel connection."""
        channel = await self._get_channel(channel_id, tenant_id)
        current_tokens = TokenSet.model_validate(
            self._token_store.from_channel_connection(channel, self._encryptor).model_dump()
        )

        auth = self._get_auth_class(channel.platform)

        if channel.platform == "instagram":
            # Instagram refreshes the access token directly
            new_tokens = await auth.refresh_token(current_tokens.access_token)
        else:
            if not current_tokens.refresh_token:
                raise TokenRefreshError(
                    "No refresh token available",
                    platform=channel.platform,
                )
            new_tokens = await auth.refresh_token(current_tokens.refresh_token)

        # Preserve existing refresh token if platform didn't return a new one
        if not new_tokens.refresh_token and current_tokens.refresh_token:
            new_tokens.refresh_token = current_tokens.refresh_token

        # Persist
        await self._token_store.persist_tokens(channel_id, new_tokens)
        channel.last_refresh_at = datetime.utcnow()
        await self.db.flush()

        return new_tokens

    async def check_health(
        self,
        channel_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> dict:
        """Full capability health check for a channel connection."""
        channel = await self._get_channel(channel_id, tenant_id)
        tokens = self._token_store.from_channel_connection(channel, self._encryptor)

        # Token status
        if not tokens.access_token:
            token_status = "missing"
        elif tokens.is_expired:
            token_status = "expired"
        elif tokens.needs_refresh:
            token_status = "needs_refresh"
        else:
            token_status = "valid"

        # Scope analysis
        required = self.get_required_scopes(channel.platform)
        granted = channel.granted_scopes or []
        missing = self.diff_granted_vs_required(granted, required)

        # Capabilities
        capabilities = self._compute_capabilities(channel.platform, granted)

        health = {
            "channel_id": str(channel_id),
            "platform": channel.platform,
            "token_status": token_status,
            "scopes_status": "complete" if not missing else "partial",
            "missing_scopes": missing,
            "capabilities": capabilities,
            "publish_capable": capabilities.get("can_publish", False),
            "needs_reconnect": token_status in ("missing", "expired") and not channel.refresh_token,
            "last_refresh_at": channel.last_refresh_at.isoformat() if channel.last_refresh_at else None,
        }

        # Update health check record
        channel.last_health_check_at = datetime.utcnow()
        channel.last_health_status = token_status
        channel.capabilities = capabilities
        await self.db.flush()

        return health

    async def disconnect_channel(
        self,
        channel_id: uuid.UUID,
        tenant_id: uuid.UUID,
        reason: str = "user_requested",
    ) -> None:
        """Best-effort upstream revocation, then clear local tokens."""
        channel = await self._get_channel(channel_id, tenant_id)

        # Best-effort upstream revocation
        revoked = await self.revoke_channel_token(channel_id, tenant_id)
        if not revoked:
            logger.warning("Upstream revocation failed for channel %s — clearing locally", channel_id)

        # Clear local tokens
        channel.access_token = None
        channel.refresh_token = None
        channel.token_expires_at = None
        channel.is_active = False
        channel.disconnect_reason = reason
        channel.granted_scopes = []
        channel.capabilities = {}
        await self.db.flush()

    async def revoke_channel_token(
        self,
        channel_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> bool:
        """Attempt to revoke token upstream. Returns True if successful."""
        channel = await self._get_channel(channel_id, tenant_id)
        tokens = self._token_store.from_channel_connection(channel, self._encryptor)

        import httpx

        try:
            if channel.platform == "youtube" and tokens.access_token:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "https://oauth2.googleapis.com/revoke",
                        params={"token": tokens.access_token},
                    )
                    return resp.status_code == 200

            elif channel.platform == "tiktok" and tokens.access_token:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "https://open.tiktokapis.com/v2/oauth/revoke/",
                        data={
                            "client_key": self.settings.tiktok_client_key,
                            "client_secret": self.settings.tiktok_client_secret,
                            "token": tokens.access_token,
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                    return resp.status_code == 200

            elif channel.platform == "twitter" and tokens.access_token:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        "https://api.twitter.com/2/oauth2/revoke",
                        data={
                            "client_id": self.settings.twitter_client_id,
                            "token": tokens.access_token,
                            "token_type_hint": "access_token",
                        },
                        auth=(self.settings.twitter_client_id, self.settings.twitter_client_secret)
                        if self.settings.twitter_client_secret else None,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                    return resp.status_code == 200

            elif channel.platform == "instagram":
                # Instagram has no revoke API — local clear only
                return True

        except Exception as exc:
            logger.warning("Upstream token revocation failed: %s", exc)
            return False

        return False

    # ── Scope / Capability helpers ───────────────────────────────

    @staticmethod
    def get_required_scopes(platform: str) -> list[str]:
        return REQUIRED_SCOPES.get(platform, [])

    @staticmethod
    def diff_granted_vs_required(granted: list[str], required: list[str]) -> list[str]:
        return [s for s in required if s not in granted]

    @staticmethod
    def _compute_capabilities(platform: str, granted_scopes: list[str]) -> dict:
        """Compute what the connection can do based on granted scopes."""
        platform_caps = SCOPE_CAPABILITIES.get(platform, {})
        result = {}
        for cap_name, required_scopes in platform_caps.items():
            if not required_scopes:
                result[cap_name] = False
            else:
                result[cap_name] = all(s in granted_scopes for s in required_scopes)
        return result

    def validate_capabilities(self, channel: ChannelConnectionModel) -> dict:
        caps = self._compute_capabilities(channel.platform, channel.granted_scopes or [])
        caps["needs_reconnect"] = not channel.is_active or not channel.access_token
        return caps

    # ── DB helpers ───────────────────────────────────────────────

    async def _get_channel(
        self, channel_id: uuid.UUID, tenant_id: uuid.UUID
    ) -> ChannelConnectionModel:
        result = await self.db.execute(
            select(ChannelConnectionModel).where(
                ChannelConnectionModel.id == channel_id,
                ChannelConnectionModel.tenant_id == tenant_id,
            )
        )
        channel = result.scalar_one_or_none()
        if not channel:
            raise ValueError(f"Channel {channel_id} not found")
        return channel

    async def _upsert_channel(
        self,
        tenant_id: uuid.UUID,
        artist_id: uuid.UUID,
        platform: str,
        tokens: TokenSet,
        capabilities: dict,
    ) -> ChannelConnectionModel:
        """Create or update a channel connection after OAuth."""
        # Look for existing connection for same tenant+artist+platform+account
        stmt = select(ChannelConnectionModel).where(
            ChannelConnectionModel.tenant_id == tenant_id,
            ChannelConnectionModel.artist_id == artist_id,
            ChannelConnectionModel.platform == platform,
        )
        if tokens.account_id:
            stmt = stmt.where(
                ChannelConnectionModel.platform_account_id == tokens.account_id
            )

        result = await self.db.execute(stmt)
        channel = result.scalar_one_or_none()

        if channel is None:
            channel = ChannelConnectionModel(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                artist_id=artist_id,
                platform=platform,
                integration_mode="automatic",
                platform_account_id=tokens.account_id or None,
                display_name=tokens.extra.get("display_name"),
                avatar_url=tokens.extra.get("avatar_url"),
                is_active=True,
                granted_scopes=tokens.scopes,
                capabilities=capabilities,
            )
            self.db.add(channel)
            await self.db.flush()
        else:
            channel.is_active = True
            channel.granted_scopes = tokens.scopes
            channel.capabilities = capabilities
            channel.disconnect_reason = None
            if tokens.extra.get("display_name"):
                channel.display_name = tokens.extra["display_name"]
            if tokens.extra.get("avatar_url"):
                channel.avatar_url = tokens.extra["avatar_url"]
            if tokens.account_id:
                channel.platform_account_id = tokens.account_id

        # Persist encrypted tokens
        await self._token_store.persist_tokens(channel.id, tokens)

        # Refresh so server-generated columns (created_at, updated_at) are
        # loaded before any downstream serialization — avoids MissingGreenlet.
        # Covers both create and update paths, since persist_tokens may flush.
        await self.db.refresh(channel)

        return channel

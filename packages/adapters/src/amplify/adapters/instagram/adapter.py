"""Instagram platform adapter — unified interface implementing PlatformAdapter."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from amplify.adapters.base import (
    AuthenticationError,
    BaseAdapter,
    Comment,
    ConnectionHealth,
    ConnectionStatus,
    FetchedPost,
    FetchError,
    MetricSnapshot,
    PublishResult,
    TokenExpiredError,
)
from amplify.adapters.instagram.auth import InstagramAuth
from amplify.adapters.instagram.comments import InstagramComments
from amplify.adapters.instagram.insights import InstagramInsights
from amplify.adapters.instagram.publish import InstagramPublisher
from amplify.adapters.token_store import TokenSet, TokenStore

logger = logging.getLogger(__name__)

GRAPH_API = "https://graph.instagram.com/v21.0"


class InstagramAdapter(BaseAdapter):
    """Unified Instagram adapter implementing the PlatformAdapter protocol.

    Credentials dict for connect():
        access_token: str (required)
        account_id: str (required — Instagram Business Account ID)
        refresh_token: str (optional)
        token_expires_at: datetime | str (optional)
        client_id: str (optional — needed for token refresh)
        client_secret: str (optional — needed for token refresh)
    """

    platform = "instagram"

    def __init__(
        self,
        *,
        dry_run: bool = False,
        token_store: TokenStore | None = None,
    ) -> None:
        super().__init__(dry_run=dry_run)
        self._token_store = token_store or TokenStore()
        self._publisher: InstagramPublisher | None = None
        self._comments: InstagramComments | None = None
        self._insights: InstagramInsights | None = None
        self._auth: InstagramAuth | None = None

    async def connect(self, credentials: dict[str, Any]) -> ConnectionHealth:
        """Initialize adapter with credentials and validate."""
        self._access_token = credentials.get("access_token", "")
        self._account_id = credentials.get("account_id", "")
        self._refresh_token = credentials.get("refresh_token", "")

        expires = credentials.get("token_expires_at")
        if isinstance(expires, str):
            self._token_expires_at = datetime.fromisoformat(expires)
        elif isinstance(expires, datetime):
            self._token_expires_at = expires

        if not self._access_token or not self._account_id:
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.ERROR,
                error="access_token and account_id are required",
            )

        # Store auth client for refresh
        client_id = credentials.get("client_id", "")
        client_secret = credentials.get("client_secret", "")
        if client_id and client_secret:
            self._auth = InstagramAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=credentials.get("redirect_uri", ""),
            )

        # Initialize sub-components
        self._publisher = InstagramPublisher(self._access_token, self._account_id)
        self._comments = InstagramComments(self._access_token, self._account_id)
        self._insights = InstagramInsights(self._access_token, self._account_id)
        self._connected = True

        return await self.validate_connection()

    async def validate_connection(self) -> ConnectionHealth:
        """Validate the current connection by calling the Graph API."""
        if not self._access_token:
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.ERROR,
                error="No access token",
            )

        if self._is_token_expired():
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.EXPIRED,
                account_id=self._account_id,
                token_expires_at=self._token_expires_at,
                error="Token expired",
            )

        if self.dry_run:
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.CONNECTED,
                account_id=self._account_id,
                display_name="[dry_run]",
            )

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{GRAPH_API}/{self._account_id}",
                    params={
                        "fields": "id,username,name",
                        "access_token": self._access_token,
                    },
                )
                if resp.status_code == 190 or "OAuthException" in resp.text:
                    return ConnectionHealth(
                        platform=self.platform,
                        status=ConnectionStatus.REVOKED,
                        error="Token revoked or invalid",
                    )
                if resp.status_code >= 400:
                    return ConnectionHealth(
                        platform=self.platform,
                        status=ConnectionStatus.ERROR,
                        error=f"API error {resp.status_code}: {resp.text[:200]}",
                    )
                data = resp.json()
        except httpx.HTTPError as e:
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.ERROR,
                error=str(e),
            )

        self._display_name = data.get("username", data.get("name", ""))
        return ConnectionHealth(
            platform=self.platform,
            status=ConnectionStatus.CONNECTED,
            account_id=self._account_id,
            display_name=self._display_name,
            token_expires_at=self._token_expires_at,
        )

    async def publish(
        self,
        content: str,
        media_paths: list[str] | None = None,
        **kwargs: Any,
    ) -> PublishResult:
        """Publish content to Instagram.

        kwargs:
            media_type: "photo" | "reel" | "story" | "carousel" (default: "photo")
        """
        self._require_connected()
        self._check_token_expiry()

        media_urls = media_paths or []
        media_type = kwargs.get("media_type")

        # Auto-detect media type from file extensions if not explicitly set
        if not media_type and media_urls:
            is_video = any(
                u.lower().split("?")[0].endswith((".mp4", ".mov", ".webm"))
                for u in media_urls
            )
            if len(media_urls) >= 2:
                media_type = "carousel"
            elif is_video:
                media_type = "reel"
            else:
                media_type = "photo"
        elif not media_type:
            media_type = "photo"

        if self.dry_run:
            return self._dry_result(
                "publish",
                media_type=media_type,
                content_length=len(content),
                media_count=len(media_urls),
            )

        assert self._publisher is not None

        if media_type == "reel" and media_urls:
            post_id = await self._publisher.publish_reel(media_urls[0], content)
        elif media_type == "story" and media_urls:
            is_video = any(u.lower().split("?")[0].endswith((".mp4", ".mov")) for u in media_urls)
            post_id = await self._publisher.publish_story(media_urls[0], is_video=is_video)
        elif media_type == "carousel" and len(media_urls) >= 2:
            post_id = await self._publisher.publish_carousel(media_urls, content)
        elif media_urls:
            post_id = await self._publisher.publish_photo(media_urls[0], content)
        else:
            from amplify.adapters.base import PublishError
            raise PublishError("Instagram requires at least one media URL", platform="instagram")

        # Fetch the real permalink from Instagram
        permalink = await self._publisher.get_permalink(post_id)
        url = permalink or f"https://www.instagram.com/"

        return PublishResult(
            platform=self.platform,
            platform_post_id=post_id,
            url=url,
            status="published",
            metadata={"media_type": media_type},
        )

    async def delete_post(self, platform_post_id: str) -> bool:
        """Instagram does not support post deletion via API.

        Returns False — the user must delete from Instagram directly.
        """
        logger.warning(
            "Instagram does not support API deletion. Post %s must be deleted manually.",
            platform_post_id,
        )
        return False

    async def fetch_post(self, platform_post_id: str) -> FetchedPost:
        """Fetch a single post by its Instagram media ID."""
        self._require_connected()
        self._check_token_expiry()

        if self.dry_run:
            return FetchedPost(
                platform=self.platform,
                platform_post_id=platform_post_id,
                content_text="[dry_run] sample caption",
            )

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GRAPH_API}/{platform_post_id}",
                params={
                    "fields": "id,caption,media_url,timestamp,permalink,like_count,comments_count",
                    "access_token": self._access_token,
                },
            )
            if resp.status_code >= 400:
                raise FetchError(
                    f"Fetch failed: {resp.text[:500]}",
                    platform=self.platform,
                )
            data = resp.json()

        return FetchedPost(
            platform=self.platform,
            platform_post_id=data.get("id", platform_post_id),
            content_text=data.get("caption", ""),
            media_urls=[data["media_url"]] if "media_url" in data else [],
            engagement={
                "likes": data.get("like_count", 0),
                "comments": data.get("comments_count", 0),
            },
            raw=data,
        )

    async def fetch_comments(
        self,
        platform_post_id: str,
        limit: int = 50,
    ) -> list[Comment]:
        """Fetch comments on an Instagram post."""
        self._require_connected()
        self._check_token_expiry()

        if self.dry_run:
            return [Comment(
                platform=self.platform,
                comment_id="dry_run_comment_1",
                post_id=platform_post_id,
                author="dry_run_user",
                text="[dry_run] Great track!",
            )]

        assert self._comments is not None
        return await self._comments.get_comments(platform_post_id, limit=limit)

    async def sync_metrics(self, platform_post_id: str) -> MetricSnapshot:
        """Fetch current engagement metrics for an Instagram post."""
        self._require_connected()
        self._check_token_expiry()

        if self.dry_run:
            return MetricSnapshot(
                platform=self.platform,
                post_id=platform_post_id,
                impressions=1000,
                reach=800,
                likes=50,
                comments=10,
            )

        assert self._insights is not None
        return await self._insights.get_media_insights(platform_post_id)

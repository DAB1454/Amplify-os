"""TikTok platform adapter — unified interface implementing PlatformAdapter."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx

from amplify.adapters.base import (
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
from amplify.adapters.tiktok.analytics import TikTokAnalytics
from amplify.adapters.tiktok.auth import TikTokAuth
from amplify.adapters.tiktok.drafts import TikTokDrafts
from amplify.adapters.tiktok.publish import TikTokPublisher
from amplify.adapters.token_store import TokenStore

logger = logging.getLogger(__name__)

TT_API = "https://open.tiktokapis.com/v2"


class TikTokAdapter(BaseAdapter):
    """Unified TikTok adapter implementing the PlatformAdapter protocol.

    Credentials dict for connect():
        access_token: str (required)
        refresh_token: str (optional)
        token_expires_at: datetime | str (optional)
        open_id: str (optional — TikTok user open_id)
        client_key: str (optional — needed for token refresh)
        client_secret: str (optional — needed for token refresh)
    """

    platform = "tiktok"

    def __init__(
        self,
        *,
        dry_run: bool = False,
        token_store: TokenStore | None = None,
    ) -> None:
        super().__init__(dry_run=dry_run)
        self._token_store = token_store or TokenStore()
        self._publisher: TikTokPublisher | None = None
        self._analytics: TikTokAnalytics | None = None
        self._drafts: TikTokDrafts | None = None
        self._auth: TikTokAuth | None = None

    async def connect(self, credentials: dict[str, Any]) -> ConnectionHealth:
        """Initialize adapter with credentials and validate."""
        self._access_token = credentials.get("access_token", "")
        self._refresh_token = credentials.get("refresh_token", "")
        self._account_id = credentials.get("open_id", "")

        expires = credentials.get("token_expires_at")
        if isinstance(expires, str):
            self._token_expires_at = datetime.fromisoformat(expires)
        elif isinstance(expires, datetime):
            self._token_expires_at = expires

        if not self._access_token:
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.ERROR,
                error="access_token is required",
            )

        client_key = credentials.get("client_key", "")
        client_secret = credentials.get("client_secret", "")
        if client_key and client_secret:
            self._auth = TikTokAuth(
                client_key=client_key,
                client_secret=client_secret,
                redirect_uri=credentials.get("redirect_uri", ""),
            )

        self._publisher = TikTokPublisher(self._access_token)
        self._analytics = TikTokAnalytics(self._access_token)
        self._drafts = TikTokDrafts(self._access_token)
        self._connected = True

        return await self.validate_connection()

    async def validate_connection(self) -> ConnectionHealth:
        """Validate the current connection."""
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
                    f"{TT_API}/user/info/",
                    headers={
                        "Authorization": f"Bearer {self._access_token}",
                    },
                    params={"fields": "open_id,display_name,avatar_url"},
                )
                if resp.status_code == 401:
                    return ConnectionHealth(
                        platform=self.platform,
                        status=ConnectionStatus.REVOKED,
                        error="Token invalid or revoked",
                    )
                if resp.status_code >= 400:
                    return ConnectionHealth(
                        platform=self.platform,
                        status=ConnectionStatus.ERROR,
                        error=f"API error {resp.status_code}",
                    )
                data = resp.json()
        except httpx.HTTPError as e:
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.ERROR,
                error=str(e),
            )

        user = data.get("data", {}).get("user", {})
        self._display_name = user.get("display_name", "")
        self._account_id = self._account_id or user.get("open_id", "")

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
        """Publish a video to TikTok.

        kwargs:
            privacy_level: "SELF_ONLY" | "MUTUAL_FOLLOW_FRIENDS" | "PUBLIC_TO_EVERYONE"
            as_draft: bool — save as draft instead of publishing
        """
        self._require_connected()
        self._check_token_expiry()

        if self.dry_run:
            return self._dry_result(
                "publish",
                content_length=len(content),
                media_count=len(media_paths or []),
                as_draft=kwargs.get("as_draft", False),
            )

        if not media_paths:
            from amplify.adapters.base import PublishError
            raise PublishError("TikTok requires a video file", platform="tiktok")

        assert self._publisher is not None
        publish_id = await self._publisher.upload_video(
            media_paths[0],
            content,
            privacy_level=kwargs.get("privacy_level", "SELF_ONLY"),
            as_draft=kwargs.get("as_draft", False),
        )

        return PublishResult(
            platform=self.platform,
            platform_post_id=publish_id,
            status="processing",
            metadata={
                "privacy_level": kwargs.get("privacy_level", "SELF_ONLY"),
                "as_draft": kwargs.get("as_draft", False),
            },
        )

    async def delete_post(self, platform_post_id: str) -> bool:
        """TikTok does not support video deletion via API.

        Returns False — the user must delete from TikTok directly.
        """
        logger.warning(
            "TikTok does not support API deletion. Video %s must be deleted manually.",
            platform_post_id,
        )
        return False

    async def fetch_post(self, platform_post_id: str) -> FetchedPost:
        """Fetch a single video by ID."""
        self._require_connected()
        self._check_token_expiry()

        if self.dry_run:
            return FetchedPost(
                platform=self.platform,
                platform_post_id=platform_post_id,
                content_text="[dry_run] sample caption",
            )

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TT_API}/video/query/",
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "filters": {"video_ids": [platform_post_id]},
                    "fields": [
                        "id", "title", "like_count", "comment_count",
                        "share_count", "view_count", "create_time",
                    ],
                },
            )
            if resp.status_code >= 400:
                raise FetchError(
                    f"Fetch failed: {resp.text[:500]}",
                    platform=self.platform,
                )
            data = resp.json()

        videos = data.get("data", {}).get("videos", [])
        if not videos:
            raise FetchError(f"Video not found: {platform_post_id}", platform=self.platform)

        v = videos[0]
        return FetchedPost(
            platform=self.platform,
            platform_post_id=v.get("id", platform_post_id),
            content_text=v.get("title", ""),
            engagement={
                "likes": v.get("like_count", 0),
                "comments": v.get("comment_count", 0),
                "shares": v.get("share_count", 0),
                "views": v.get("view_count", 0),
            },
            raw=v,
        )

    async def fetch_comments(
        self,
        platform_post_id: str,
        limit: int = 50,
    ) -> list[Comment]:
        """Fetch comments on a TikTok video."""
        self._require_connected()
        self._check_token_expiry()

        if self.dry_run:
            return [Comment(
                platform=self.platform,
                comment_id="dry_run_comment_1",
                post_id=platform_post_id,
                author="dry_run_user",
                text="[dry_run] Love this!",
            )]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TT_API}/video/comment/list/",
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "video_id": platform_post_id,
                    "max_count": limit,
                },
            )
            if resp.status_code >= 400:
                raise FetchError(
                    f"Comments fetch failed: {resp.text[:500]}",
                    platform=self.platform,
                )
            data = resp.json()

        comments = []
        for c in data.get("data", {}).get("comments", []):
            comments.append(Comment(
                platform=self.platform,
                comment_id=str(c.get("id", "")),
                post_id=platform_post_id,
                author=c.get("user", {}).get("display_name", ""),
                text=c.get("text", ""),
                like_count=c.get("like_count", 0),
                raw=c,
            ))
        return comments

    async def sync_metrics(self, platform_post_id: str) -> MetricSnapshot:
        """Fetch current metrics for a TikTok video."""
        self._require_connected()
        self._check_token_expiry()

        if self.dry_run:
            return MetricSnapshot(
                platform=self.platform,
                post_id=platform_post_id,
                views=5000,
                likes=200,
                comments=30,
                shares=15,
            )

        assert self._analytics is not None
        return await self._analytics.get_video_analytics(platform_post_id)

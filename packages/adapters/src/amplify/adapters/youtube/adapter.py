"""YouTube platform adapter — unified interface implementing PlatformAdapter."""

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
)
from amplify.adapters.token_store import TokenStore
from amplify.adapters.youtube.auth import YouTubeAuth
from amplify.adapters.youtube.comments import YouTubeComments
from amplify.adapters.youtube.metadata import YouTubeMetadata
from amplify.adapters.youtube.upload import YouTubeUploader

logger = logging.getLogger(__name__)

YT_API = "https://www.googleapis.com/youtube/v3"


class YouTubeAdapter(BaseAdapter):
    """Unified YouTube adapter implementing the PlatformAdapter protocol.

    Credentials dict for connect():
        access_token: str (required)
        refresh_token: str (optional)
        token_expires_at: datetime | str (optional)
        channel_id: str (optional — YouTube channel ID)
        client_id: str (optional — needed for token refresh)
        client_secret: str (optional — needed for token refresh)
    """

    platform = "youtube"

    def __init__(
        self,
        *,
        dry_run: bool = False,
        token_store: TokenStore | None = None,
    ) -> None:
        super().__init__(dry_run=dry_run)
        self._token_store = token_store or TokenStore()
        self._uploader: YouTubeUploader | None = None
        self._comments: YouTubeComments | None = None
        self._metadata: YouTubeMetadata | None = None
        self._auth: YouTubeAuth | None = None

    async def connect(self, credentials: dict[str, Any]) -> ConnectionHealth:
        """Initialize adapter with credentials and validate."""
        self._access_token = credentials.get("access_token", "")
        self._refresh_token = credentials.get("refresh_token", "")
        self._account_id = credentials.get("channel_id", "")

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

        client_id = credentials.get("client_id", "")
        client_secret = credentials.get("client_secret", "")
        if client_id and client_secret:
            self._auth = YouTubeAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=credentials.get("redirect_uri", ""),
            )

        self._uploader = YouTubeUploader(self._access_token)
        self._comments = YouTubeComments(self._access_token)
        self._metadata = YouTubeMetadata(self._access_token)
        self._connected = True

        return await self.validate_connection()

    async def validate_connection(self) -> ConnectionHealth:
        """Validate the current connection by calling the YouTube API."""
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
                    f"{YT_API}/channels",
                    headers={"Authorization": f"Bearer {self._access_token}"},
                    params={"part": "snippet", "mine": "true"},
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

        items = data.get("items", [])
        if items:
            snippet = items[0].get("snippet", {})
            self._display_name = snippet.get("title", "")
            self._account_id = self._account_id or items[0].get("id", "")

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
        """Upload a video to YouTube.

        kwargs:
            title: str (defaults to first 100 chars of content)
            tags: list[str]
            categoryId: str (default "10" = Music)
            privacyStatus: "private" | "unlisted" | "public"
        """
        self._require_connected()
        self._check_token_expiry()

        if self.dry_run:
            return self._dry_result(
                "publish",
                content_length=len(content),
                media_count=len(media_paths or []),
                title=kwargs.get("title", ""),
            )

        if not media_paths:
            from amplify.adapters.base import PublishError
            raise PublishError("YouTube requires a video file", platform="youtube")

        assert self._uploader is not None
        metadata = {
            "title": kwargs.get("title", content[:100]),
            "description": content,
            "tags": kwargs.get("tags", []),
            "categoryId": kwargs.get("categoryId", "10"),
            "privacyStatus": kwargs.get("privacyStatus", "private"),
        }

        video_id = await self._uploader.upload_video(media_paths[0], metadata)

        return PublishResult(
            platform=self.platform,
            platform_post_id=video_id,
            url=f"https://youtu.be/{video_id}",
            status="published",
            metadata=metadata,
        )

    async def fetch_post(self, platform_post_id: str) -> FetchedPost:
        """Fetch a single video by its YouTube video ID."""
        self._require_connected()
        self._check_token_expiry()

        if self.dry_run:
            return FetchedPost(
                platform=self.platform,
                platform_post_id=platform_post_id,
                content_text="[dry_run] sample description",
            )

        assert self._metadata is not None
        data = await self._metadata.get_video_details(platform_post_id)

        snippet = data.get("snippet", {})
        stats = data.get("statistics", {})

        return FetchedPost(
            platform=self.platform,
            platform_post_id=data.get("id", platform_post_id),
            content_text=snippet.get("description", ""),
            media_urls=[snippet.get("thumbnails", {}).get("high", {}).get("url", "")],
            engagement={
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
            },
            raw=data,
        )

    async def fetch_comments(
        self,
        platform_post_id: str,
        limit: int = 50,
    ) -> list[Comment]:
        """Fetch comments on a YouTube video."""
        self._require_connected()
        self._check_token_expiry()

        if self.dry_run:
            return [Comment(
                platform=self.platform,
                comment_id="dry_run_comment_1",
                post_id=platform_post_id,
                author="dry_run_user",
                text="[dry_run] Amazing video!",
            )]

        assert self._comments is not None
        return await self._comments.get_comments(platform_post_id, limit=limit)

    async def sync_metrics(self, platform_post_id: str) -> MetricSnapshot:
        """Fetch current metrics for a YouTube video."""
        self._require_connected()
        self._check_token_expiry()

        if self.dry_run:
            return MetricSnapshot(
                platform=self.platform,
                post_id=platform_post_id,
                views=10000,
                likes=300,
                comments=50,
            )

        assert self._metadata is not None
        data = await self._metadata.get_video_details(platform_post_id)
        stats = data.get("statistics", {})

        return MetricSnapshot(
            platform=self.platform,
            post_id=platform_post_id,
            views=int(stats.get("viewCount", 0)),
            likes=int(stats.get("likeCount", 0)),
            comments=int(stats.get("commentCount", 0)),
            extra={
                "favorites": int(stats.get("favoriteCount", 0)),
                "dislikes": int(stats.get("dislikeCount", 0)),
            },
        )

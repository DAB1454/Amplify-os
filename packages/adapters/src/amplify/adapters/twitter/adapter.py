"""X (Twitter) API v2 adapter.

Supports:
- Publishing tweets (text-only and with media)
- Fetching tweet engagement metrics
- Fetching comments (replies)
- Connection health checks

Twitter API v2 endpoints used:
- POST /2/tweets — create a tweet
- GET /2/tweets/:id — fetch tweet
- GET /2/tweets/:id/quote_tweets — fetch quotes
- GET /2/tweets/search/recent — fetch replies
- POST /1.1/media/upload.json — upload media (v1.1, requires media.upload scope)
- GET /2/users/me — validate connection
"""

from __future__ import annotations

import logging
import mimetypes
from datetime import datetime
from typing import Any

import httpx

from amplify.adapters.base import (
    BaseAdapter,
    Comment,
    ConnectionHealth,
    ConnectionStatus,
    FetchedPost,
    MetricSnapshot,
    PublishError,
    PublishResult,
    TokenExpiredError,
)

logger = logging.getLogger(__name__)

API_V2 = "https://api.twitter.com/2"
UPLOAD_V1 = "https://upload.twitter.com/1.1/media/upload.json"


class TwitterAdapter(BaseAdapter):
    """X (Twitter) platform adapter using API v2."""

    platform = "twitter"

    def __init__(self, dry_run: bool = False) -> None:
        super().__init__(dry_run=dry_run)
        self._access_token: str = ""
        self._account_id: str = ""

    # ── Connection ──────────────────────────────────────────────

    async def connect(self, credentials: dict[str, Any]) -> ConnectionHealth:
        self._access_token = credentials.get("access_token", "")
        self._account_id = credentials.get("account_id", "")

        raw_expires = credentials.get("token_expires_at")
        if isinstance(raw_expires, str):
            try:
                self._token_expires_at = datetime.fromisoformat(raw_expires)
            except ValueError:
                pass
        elif isinstance(raw_expires, datetime):
            self._token_expires_at = raw_expires

        self._connected = True
        return await self.validate_connection()

    async def validate_connection(self) -> ConnectionHealth:
        if self._is_token_expired():
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.EXPIRED,
                error="Token expired",
            )

        if self.dry_run:
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.CONNECTED,
                account_id=self._account_id,
                display_name="[dry-run] Twitter",
            )

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{API_V2}/users/me",
                    headers=self._auth_headers(),
                    params={"user.fields": "name,username"},
                    timeout=10,
                )
            if resp.status_code == 401:
                return ConnectionHealth(
                    platform=self.platform,
                    status=ConnectionStatus.REVOKED,
                    error="Token revoked or expired",
                )
            if resp.status_code != 200:
                return ConnectionHealth(
                    platform=self.platform,
                    status=ConnectionStatus.ERROR,
                    error=f"API error {resp.status_code}: {resp.text[:200]}",
                )

            data = resp.json().get("data", {})
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.CONNECTED,
                account_id=data.get("id", self._account_id),
                display_name=data.get("name", ""),
            )

        except Exception as exc:
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.ERROR,
                error=str(exc),
            )

    # ── Publish ─────────────────────────────────────────────────

    async def publish(
        self,
        content: str,
        media_paths: list[str] | None = None,
        **kwargs: Any,
    ) -> PublishResult:
        self._require_connected()
        self._check_token_expiry()

        if self.dry_run:
            return self._dry_result()

        # Upload media if provided
        media_ids: list[str] = []
        for path in (media_paths or []):
            if path:
                try:
                    mid = await self._upload_media(path)
                    if mid:
                        media_ids.append(mid)
                except Exception as exc:
                    logger.warning("Twitter media upload failed for %s: %s", path, exc)

        # Build tweet payload
        payload: dict[str, Any] = {"text": content[:280]}
        if media_ids:
            payload["media"] = {"media_ids": media_ids}

        # Optional: reply to a tweet (for threads)
        reply_to = kwargs.get("reply_to_tweet_id")
        if reply_to:
            payload["reply"] = {"in_reply_to_tweet_id": reply_to}

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{API_V2}/tweets",
                    headers={**self._auth_headers(), "Content-Type": "application/json"},
                    json=payload,
                    timeout=30,
                )

            if resp.status_code in (401, 403):
                raise TokenExpiredError(
                    f"Twitter token expired or revoked ({resp.status_code}): {resp.text[:300]}",
                    platform=self.platform,
                )
            if resp.status_code == 402:
                raise PublishError(
                    f"X/Twitter API credits depleted — add credits at developer.x.com. Raw: {resp.text[:300]}",
                    platform=self.platform,
                    permanent=True,
                )
            if resp.status_code not in (200, 201):
                raise PublishError(
                    f"Tweet creation failed ({resp.status_code}): {resp.text[:300]}",
                    platform=self.platform,
                )

            tweet_data = resp.json().get("data", {})
            tweet_id = tweet_data.get("id", "")

            return PublishResult(
                platform=self.platform,
                platform_post_id=tweet_id,
                url=f"https://x.com/i/status/{tweet_id}" if tweet_id else "",
                status="published",
            )

        except PublishError:
            raise
        except Exception as exc:
            raise PublishError(
                f"Tweet creation failed: {exc}",
                platform=self.platform,
            ) from exc

    async def _upload_media(self, media_url: str) -> str | None:
        """Upload media via Twitter v1.1 media upload endpoint.

        Accepts a URL — downloads then uploads to Twitter.
        Returns the media_id string or None on failure.
        """
        async with httpx.AsyncClient() as client:
            # Download the media
            dl_resp = await client.get(media_url, timeout=30)
            if dl_resp.status_code != 200:
                logger.warning("Failed to download media from %s", media_url)
                return None

            media_bytes = dl_resp.content
            content_type = dl_resp.headers.get("content-type", "")
            if not content_type:
                content_type = mimetypes.guess_type(media_url)[0] or "image/jpeg"

            # Upload to Twitter
            # For images < 5MB, simple upload works
            resp = await client.post(
                UPLOAD_V1,
                headers={"Authorization": f"Bearer {self._access_token}"},
                files={"media": ("media", media_bytes, content_type)},
                timeout=60,
            )

            if resp.status_code not in (200, 201, 202):
                logger.warning("Twitter media upload failed: %s %s", resp.status_code, resp.text[:200])
                return None

            return resp.json().get("media_id_string")

    # ── Fetch ───────────────────────────────────────────────────

    async def fetch_post(self, platform_post_id: str) -> FetchedPost:
        self._require_connected()
        self._check_token_expiry()

        if self.dry_run:
            return FetchedPost(platform=self.platform, platform_post_id=platform_post_id)

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{API_V2}/tweets/{platform_post_id}",
                headers=self._auth_headers(),
                params={
                    "tweet.fields": "created_at,text,public_metrics",
                },
                timeout=10,
            )

        if resp.status_code != 200:
            return FetchedPost(platform=self.platform, platform_post_id=platform_post_id)

        data = resp.json().get("data", {})
        metrics = data.get("public_metrics", {})
        published_at = None
        if data.get("created_at"):
            try:
                published_at = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
            except ValueError:
                pass

        return FetchedPost(
            platform=self.platform,
            platform_post_id=platform_post_id,
            content_text=data.get("text", ""),
            published_at=published_at,
            engagement={
                "impressions": metrics.get("impression_count", 0),
                "likes": metrics.get("like_count", 0),
                "comments": metrics.get("reply_count", 0),
                "shares": metrics.get("retweet_count", 0) + metrics.get("quote_count", 0),
                "views": metrics.get("impression_count", 0),
                "bookmarks": metrics.get("bookmark_count", 0),
            },
            raw=data,
        )

    # ── Comments (Replies) ──────────────────────────────────────

    async def fetch_comments(
        self,
        platform_post_id: str,
        limit: int = 50,
    ) -> list[Comment]:
        self._require_connected()
        self._check_token_expiry()

        if self.dry_run:
            return []

        # Search for replies to this tweet
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{API_V2}/tweets/search/recent",
                    headers=self._auth_headers(),
                    params={
                        "query": f"conversation_id:{platform_post_id} is:reply",
                        "tweet.fields": "author_id,created_at,text,public_metrics",
                        "max_results": min(limit, 100),
                    },
                    timeout=10,
                )

            if resp.status_code != 200:
                return []

            comments = []
            for tweet in resp.json().get("data", []):
                created_at = None
                if tweet.get("created_at"):
                    try:
                        created_at = datetime.fromisoformat(tweet["created_at"].replace("Z", "+00:00"))
                    except ValueError:
                        pass

                metrics = tweet.get("public_metrics", {})
                comments.append(Comment(
                    platform=self.platform,
                    comment_id=tweet.get("id", ""),
                    post_id=platform_post_id,
                    author=tweet.get("author_id", ""),
                    text=tweet.get("text", ""),
                    created_at=created_at,
                    reply_count=metrics.get("reply_count", 0),
                    like_count=metrics.get("like_count", 0),
                    is_reply=True,
                    raw=tweet,
                ))
            return comments

        except Exception as exc:
            logger.warning("Failed to fetch Twitter replies: %s", exc)
            return []

    # ── Metrics ─────────────────────────────────────────────────

    async def sync_metrics(self, platform_post_id: str) -> MetricSnapshot:
        self._require_connected()
        self._check_token_expiry()

        if self.dry_run:
            return MetricSnapshot(platform=self.platform, post_id=platform_post_id)

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{API_V2}/tweets/{platform_post_id}",
                    headers=self._auth_headers(),
                    params={
                        "tweet.fields": "public_metrics,non_public_metrics,organic_metrics",
                    },
                    timeout=10,
                )

            if resp.status_code != 200:
                logger.warning("Twitter metrics fetch failed for %s: %s", platform_post_id, resp.text[:200])
                return MetricSnapshot(platform=self.platform, post_id=platform_post_id)

            data = resp.json().get("data", {})
            pub = data.get("public_metrics", {})
            # non_public_metrics requires owner context — may not be available
            non_pub = data.get("non_public_metrics", {})
            organic = data.get("organic_metrics", {})

            impressions = (
                non_pub.get("impression_count", 0)
                or organic.get("impression_count", 0)
                or pub.get("impression_count", 0)
            )

            return MetricSnapshot(
                platform=self.platform,
                post_id=platform_post_id,
                impressions=impressions,
                likes=pub.get("like_count", 0),
                comments=pub.get("reply_count", 0),
                shares=pub.get("retweet_count", 0) + pub.get("quote_count", 0),
                views=impressions,
                clicks=non_pub.get("url_link_clicks", 0) + non_pub.get("user_profile_clicks", 0),
                saves=pub.get("bookmark_count", 0),
            )

        except Exception as exc:
            logger.warning("Twitter metrics failed for %s: %s", platform_post_id, exc)
            return MetricSnapshot(platform=self.platform, post_id=platform_post_id)

    # ── Helpers ──────────────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

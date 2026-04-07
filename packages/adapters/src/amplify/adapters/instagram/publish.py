"""Publish content to Instagram via the Graph API.

Supports photo, reel, story, and carousel posts via the Container-based
publish flow:
1. Create media container(s) with content
2. Wait for container processing (for video)
3. Publish the container
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from amplify.adapters.base import PublishError, RateLimitError

logger = logging.getLogger(__name__)

GRAPH_API = "https://graph.instagram.com/v21.0"
CONTAINER_POLL_INTERVAL = 3  # seconds
# Cap below the worker job timeout (600s) so we always raise a clean
# PublishError on slow containers instead of getting killed mid-publish.
# 80 attempts * 3s = 240s, leaving plenty of margin under 600s.
CONTAINER_POLL_MAX = 80  # max attempts (~4 minutes)


class InstagramPublisher:
    """Publish photos, reels, stories, and carousels to Instagram."""

    def __init__(self, access_token: str, account_id: str) -> None:
        if not access_token or not account_id:
            raise ValueError("access_token and account_id are required")
        self.access_token = access_token
        self.account_id = account_id

    async def _api_post(self, endpoint: str, data: dict) -> dict:
        """Make a POST request to the Graph API."""
        data["access_token"] = self.access_token
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{GRAPH_API}{endpoint}", data=data)
            self._check_response(resp)
            return resp.json()

    async def _api_get(self, endpoint: str, params: dict | None = None) -> dict:
        """Make a GET request to the Graph API."""
        params = params or {}
        params["access_token"] = self.access_token
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{GRAPH_API}{endpoint}", params=params)
            self._check_response(resp)
            return resp.json()

    def _check_response(self, resp: httpx.Response) -> None:
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "60"))
            raise RateLimitError(
                "Instagram rate limit exceeded",
                platform="instagram",
                retry_after=retry_after,
            )
        if resp.status_code >= 400:
            raise PublishError(
                f"Instagram API error {resp.status_code}: {resp.text[:500]}",
                platform="instagram",
                details={"status": resp.status_code},
            )

    async def _wait_for_container(self, container_id: str) -> None:
        """Poll until a media container finishes processing."""
        for _ in range(CONTAINER_POLL_MAX):
            data = await self._api_get(
                f"/{container_id}",
                params={"fields": "status_code"},
            )
            status = data.get("status_code", "")
            if status == "FINISHED":
                return
            if status == "ERROR":
                raise PublishError(
                    f"Container {container_id} failed processing",
                    platform="instagram",
                )
            await asyncio.sleep(CONTAINER_POLL_INTERVAL)

        raise PublishError(
            f"Container {container_id} processing timed out",
            platform="instagram",
        )

    async def find_recent_duplicate(
        self,
        caption: str,
        *,
        within_seconds: int = 600,
    ) -> str | None:
        """Return the IG media id of a recent post with an identical caption.

        Used as a defense-in-depth check before publishing: if a previous
        attempt actually succeeded on Instagram's side but the worker job died
        (timeout, container race, etc) and the post was re-queued, we'd
        otherwise post the same caption again. Look back at the most recent
        media on the account and bail if we find a match within the window.
        """
        if not caption:
            return None
        try:
            data = await self._api_get(
                f"/{self.account_id}/media",
                params={"fields": "id,caption,timestamp", "limit": "5"},
            )
        except Exception as e:
            # If the lookup itself fails, don't block the publish — just log.
            logger.warning("Recent-media lookup failed, skipping dedup check: %s", e)
            return None

        from datetime import datetime, timezone, timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(seconds=within_seconds)
        target = caption.strip()

        for item in data.get("data", []):
            ts = item.get("timestamp", "")
            existing_caption = (item.get("caption") or "").strip()
            if not ts or not existing_caption:
                continue
            try:
                # IG timestamps look like 2026-04-07T22:15:00+0000
                posted_at = datetime.fromisoformat(ts.replace("+0000", "+00:00"))
            except ValueError:
                continue
            if posted_at < cutoff:
                continue
            if existing_caption == target:
                return item.get("id")
        return None

    async def get_permalink(self, media_id: str) -> str | None:
        """Fetch the permalink for a published media item.

        Retries a few times with backoff since Instagram may not have the
        permalink available immediately after publishing.
        """
        for attempt in range(4):
            try:
                if attempt > 0:
                    await asyncio.sleep(attempt * 2)
                data = await self._api_get(
                    f"/{media_id}",
                    params={"fields": "permalink"},
                )
                link = data.get("permalink")
                if link:
                    return link
            except Exception:
                pass
        return None

    async def publish_photo(self, image_url: str, caption: str) -> str:
        """Publish a single photo post. Returns the media ID."""
        # Step 1: Create container
        container = await self._api_post(
            f"/{self.account_id}/media",
            {"image_url": image_url, "caption": caption},
        )
        container_id = container["id"]

        # Step 2: Wait for container processing
        await self._wait_for_container(container_id)

        # Step 3: Publish container
        result = await self._api_post(
            f"/{self.account_id}/media_publish",
            {"creation_id": container_id},
        )
        return result["id"]

    async def publish_reel(self, video_url: str, caption: str) -> str:
        """Publish a reel. Returns the media ID."""
        container = await self._api_post(
            f"/{self.account_id}/media",
            {
                "video_url": video_url,
                "caption": caption,
                "media_type": "REELS",
            },
        )
        container_id = container["id"]
        await self._wait_for_container(container_id)

        result = await self._api_post(
            f"/{self.account_id}/media_publish",
            {"creation_id": container_id},
        )
        return result["id"]

    async def publish_story(self, media_url: str, *, is_video: bool = False) -> str:
        """Publish a story. Returns the media ID."""
        data: dict[str, Any] = {"media_type": "STORIES"}
        if is_video:
            data["video_url"] = media_url
        else:
            data["image_url"] = media_url

        container = await self._api_post(f"/{self.account_id}/media", data)
        container_id = container["id"]

        if is_video:
            await self._wait_for_container(container_id)

        result = await self._api_post(
            f"/{self.account_id}/media_publish",
            {"creation_id": container_id},
        )
        return result["id"]

    async def publish_carousel(self, media_urls: list[str], caption: str) -> str:
        """Publish a carousel post. Returns the media ID."""
        if len(media_urls) < 2:
            raise PublishError(
                "Carousel requires at least 2 media items",
                platform="instagram",
            )

        # Instagram carousels support max 10 items
        if len(media_urls) > 10:
            media_urls = media_urls[:10]

        # Step 1: Create child containers and wait for each to process
        children_ids = []
        for url in media_urls:
            is_video = any(url.lower().endswith(ext) for ext in (".mp4", ".mov"))
            data: dict[str, str] = {"is_carousel_item": "true"}
            if is_video:
                data["video_url"] = url
                data["media_type"] = "VIDEO"
            else:
                data["image_url"] = url

            child = await self._api_post(f"/{self.account_id}/media", data)
            await self._wait_for_container(child["id"])
            children_ids.append(child["id"])

        # Step 2: Create carousel container
        container = await self._api_post(
            f"/{self.account_id}/media",
            {
                "media_type": "CAROUSEL",
                "caption": caption,
                "children": ",".join(children_ids),
            },
        )

        # Step 3: Wait for carousel container processing, then publish
        await self._wait_for_container(container["id"])
        result = await self._api_post(
            f"/{self.account_id}/media_publish",
            {"creation_id": container["id"]},
        )
        return result["id"]

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
CONTAINER_POLL_MAX = 60  # max attempts


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

    async def get_permalink(self, media_id: str) -> str | None:
        """Fetch the permalink for a published media item."""
        try:
            data = await self._api_get(
                f"/{media_id}",
                params={"fields": "permalink"},
            )
            return data.get("permalink")
        except Exception:
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

        # Step 1: Create child containers
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

        # Step 3: Publish
        result = await self._api_post(
            f"/{self.account_id}/media_publish",
            {"creation_id": container["id"]},
        )
        return result["id"]

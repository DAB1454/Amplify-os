"""Manage TikTok drafts via the Content Posting API."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from amplify.adapters.base import FetchError, PublishError
from amplify.adapters.tiktok.publish import TikTokPublisher

logger = logging.getLogger(__name__)

TT_API = "https://open.tiktokapis.com/v2"


class TikTokDrafts:
    """Save, list, and publish TikTok drafts."""

    def __init__(self, access_token: str) -> None:
        if not access_token:
            raise ValueError("access_token is required")
        self.access_token = access_token
        self._publisher = TikTokPublisher(access_token)

    async def save_draft(self, video_path: str | Path, caption: str) -> str:
        """Save a video as a draft via the upload flow with SELF_ONLY privacy."""
        return await self._publisher.upload_video(
            video_path,
            caption,
            privacy_level="SELF_ONLY",
            as_draft=True,
        )

    async def list_drafts(self) -> list[dict]:
        """List saved drafts (uses video.list endpoint)."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TT_API}/video/list/",
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Content-Type": "application/json",
                },
                json={"max_count": 20},
            )
            if resp.status_code >= 400:
                raise FetchError(
                    f"List drafts failed: {resp.text[:500]}",
                    platform="tiktok",
                )
            data = resp.json()
        return data.get("data", {}).get("videos", [])

    async def get_draft(self, publish_id: str) -> dict:
        """Get status of a specific draft/upload."""
        return await self._publisher.get_post_status(publish_id)

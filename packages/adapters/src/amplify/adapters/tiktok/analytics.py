"""TikTok analytics and metrics via the Research API."""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from amplify.adapters.base import FetchError, MetricSnapshot, RateLimitError

logger = logging.getLogger(__name__)

TT_API = "https://open.tiktokapis.com/v2"


class TikTokAnalytics:
    """Retrieve video and account analytics from TikTok."""

    def __init__(self, access_token: str) -> None:
        if not access_token:
            raise ValueError("access_token is required")
        self.access_token = access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    async def _get(self, endpoint: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TT_API}{endpoint}",
                headers=self._headers(),
                params=params or {},
            )
            if resp.status_code == 429:
                raise RateLimitError("TikTok rate limit", platform="tiktok")
            if resp.status_code >= 400:
                raise FetchError(
                    f"TikTok analytics error: {resp.text[:500]}",
                    platform="tiktok",
                )
            return resp.json()

    async def get_video_analytics(self, video_id: str) -> MetricSnapshot:
        """Get analytics for a specific video."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TT_API}/video/query/",
                headers=self._headers(),
                json={
                    "filters": {"video_ids": [video_id]},
                    "fields": [
                        "id", "like_count", "comment_count",
                        "share_count", "view_count",
                    ],
                },
            )
            if resp.status_code >= 400:
                raise FetchError(
                    f"Video query failed: {resp.text[:500]}",
                    platform="tiktok",
                )
            data = resp.json()

        videos = data.get("data", {}).get("videos", [])
        if not videos:
            return MetricSnapshot(platform="tiktok", post_id=video_id)

        v = videos[0]
        return MetricSnapshot(
            platform="tiktok",
            post_id=video_id,
            likes=v.get("like_count", 0),
            comments=v.get("comment_count", 0),
            shares=v.get("share_count", 0),
            views=v.get("view_count", 0),
        )

    async def get_account_analytics(self, period: str = "7") -> dict:
        """Get account-level analytics. Period is number of days."""
        data = await self._get(
            "/user/info/",
            params={"fields": "follower_count,following_count,likes_count,video_count"},
        )
        return data.get("data", {}).get("user", {})

"""Instagram Insights API integration."""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from amplify.adapters.base import FetchError, MetricSnapshot, RateLimitError

logger = logging.getLogger(__name__)

GRAPH_API = "https://graph.facebook.com/v19.0"


class InstagramInsights:
    """Retrieve engagement and reach insights from Instagram."""

    def __init__(self, access_token: str, account_id: str) -> None:
        if not access_token:
            raise ValueError("access_token is required")
        self.access_token = access_token
        self.account_id = account_id

    async def _get(self, endpoint: str, params: dict | None = None) -> dict:
        params = params or {}
        params["access_token"] = self.access_token
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{GRAPH_API}{endpoint}", params=params)
            if resp.status_code == 429:
                raise RateLimitError("Rate limit", platform="instagram")
            if resp.status_code >= 400:
                raise FetchError(
                    f"Insights API error: {resp.text[:500]}",
                    platform="instagram",
                )
            return resp.json()

    async def get_media_insights(self, media_id: str) -> MetricSnapshot:
        """Get insights for a specific media post."""
        data = await self._get(
            f"/{media_id}/insights",
            params={"metric": "impressions,reach,likes,comments,shares,saved"},
        )

        metrics: dict[str, int] = {}
        for item in data.get("data", []):
            name = item.get("name", "")
            values = item.get("values", [{}])
            metrics[name] = values[0].get("value", 0) if values else 0

        return MetricSnapshot(
            platform="instagram",
            post_id=media_id,
            impressions=metrics.get("impressions", 0),
            reach=metrics.get("reach", 0),
            likes=metrics.get("likes", 0),
            comments=metrics.get("comments", 0),
            shares=metrics.get("shares", 0),
            saves=metrics.get("saved", 0),
        )

    async def get_account_insights(self, period: str = "day") -> dict:
        """Get account-level insights for the given period."""
        data = await self._get(
            f"/{self.account_id}/insights",
            params={
                "metric": "impressions,reach,follower_count,profile_views",
                "period": period,
            },
        )

        result: dict[str, int] = {}
        for item in data.get("data", []):
            name = item.get("name", "")
            values = item.get("values", [{}])
            result[name] = values[0].get("value", 0) if values else 0
        return result

    async def get_post_engagement(self, media_id: str) -> dict:
        """Fetch basic engagement fields directly from the media object."""
        data = await self._get(
            f"/{media_id}",
            params={"fields": "like_count,comments_count,timestamp,permalink"},
        )
        return data

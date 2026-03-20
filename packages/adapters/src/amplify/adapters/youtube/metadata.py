"""YouTube video metadata management via the Data API v3."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from amplify.adapters.base import FetchError, PublishError, RateLimitError

logger = logging.getLogger(__name__)

YT_API = "https://www.googleapis.com/youtube/v3"


class YouTubeMetadata:
    """Read and update YouTube video metadata."""

    def __init__(self, access_token: str) -> None:
        if not access_token:
            raise ValueError("access_token is required")
        self.access_token = access_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _check(self, resp: httpx.Response, op: str) -> None:
        if resp.status_code == 403:
            raise RateLimitError(f"YouTube quota on {op}", platform="youtube")
        if resp.status_code >= 400:
            raise FetchError(
                f"YouTube {op} failed ({resp.status_code}): {resp.text[:500]}",
                platform="youtube",
            )

    async def get_video_details(self, video_id: str) -> dict:
        """Fetch full video details including snippet, status, and statistics."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{YT_API}/videos",
                headers=self._headers(),
                params={
                    "part": "snippet,status,statistics,contentDetails",
                    "id": video_id,
                },
            )
            self._check(resp, "get_video_details")
            data = resp.json()

        items = data.get("items", [])
        if not items:
            raise FetchError(f"Video not found: {video_id}", platform="youtube")
        return items[0]

    async def update_description(self, video_id: str, description: str) -> bool:
        """Update the description of a video."""
        details = await self.get_video_details(video_id)
        snippet = details.get("snippet", {})
        snippet["description"] = description

        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{YT_API}/videos",
                headers={
                    **self._headers(),
                    "Content-Type": "application/json",
                },
                params={"part": "snippet"},
                json={"id": video_id, "snippet": snippet},
            )
            if resp.status_code >= 400:
                raise PublishError(
                    f"Description update failed: {resp.text[:500]}",
                    platform="youtube",
                )
        return True

    async def set_thumbnail(self, video_id: str, image_path: str | Path) -> bool:
        """Upload and set a custom thumbnail for a video."""
        path = Path(image_path)
        if not path.exists():
            raise FetchError(f"Image not found: {path}", platform="youtube")

        async with httpx.AsyncClient() as client:
            with open(path, "rb") as f:
                resp = await client.post(
                    f"{YT_API}/thumbnails/set",
                    headers=self._headers(),
                    params={"videoId": video_id},
                    files={"media": (path.name, f, "image/png")},
                )
                if resp.status_code >= 400:
                    raise PublishError(
                        f"Thumbnail upload failed: {resp.text[:500]}",
                        platform="youtube",
                    )
        return True

"""Publish videos to TikTok via the Content Posting API v2.

Two-phase upload flow:
1. Initialize upload → get upload_url
2. Upload video bytes to upload_url
3. Optionally publish or save as draft
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

from amplify.adapters.base import PublishError, RateLimitError, ValidationError

logger = logging.getLogger(__name__)

TT_API = "https://open.tiktokapis.com/v2"
MAX_VIDEO_SIZE = 4 * 1024 * 1024 * 1024  # 4 GB


class TikTokPublisher:
    """Upload and publish videos to TikTok."""

    def __init__(self, access_token: str) -> None:
        if not access_token:
            raise ValueError("access_token is required")
        self.access_token = access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def _check_response(self, resp: httpx.Response, operation: str) -> dict:
        if resp.status_code == 429:
            raise RateLimitError(
                "TikTok rate limit exceeded",
                platform="tiktok",
                retry_after=int(resp.headers.get("Retry-After", "60")),
            )
        if resp.status_code >= 400:
            raise PublishError(
                f"TikTok {operation} failed ({resp.status_code}): {resp.text[:500]}",
                platform="tiktok",
                details={"status": resp.status_code},
            )
        data = resp.json()
        if data.get("error", {}).get("code") not in (None, "ok", 0):
            raise PublishError(
                f"TikTok {operation} error: {data['error']}",
                platform="tiktok",
                details=data.get("error", {}),
            )
        return data

    async def _init_upload(
        self,
        video_size: int,
        *,
        post_info: dict[str, Any],
    ) -> dict:
        """Initialize a video upload and get the upload URL."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TT_API}/post/publish/inbox/video/init/",
                headers=self._headers(),
                json={
                    "post_info": post_info,
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": video_size,
                    },
                },
            )
            return self._check_response(resp, "init_upload")

    async def upload_video(
        self,
        video_path: str | Path,
        caption: str,
        *,
        sounds: list[str] | None = None,
        privacy_level: str = "SELF_ONLY",
        as_draft: bool = False,
    ) -> str:
        """Upload a video to TikTok. Returns the publish_id."""
        video = Path(video_path)
        if not video.exists():
            raise ValidationError(f"Video not found: {video}", platform="tiktok")

        file_size = video.stat().st_size
        if file_size > MAX_VIDEO_SIZE:
            raise ValidationError(
                f"Video too large ({file_size} bytes, max {MAX_VIDEO_SIZE})",
                platform="tiktok",
            )

        post_info: dict[str, Any] = {
            "title": caption[:150],
            "privacy_level": privacy_level,
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        }

        # Init upload
        init_data = await self._init_upload(file_size, post_info=post_info)
        publish_id = init_data.get("data", {}).get("publish_id", "")
        upload_url = init_data.get("data", {}).get("upload_url", "")

        if not upload_url:
            raise PublishError("No upload URL returned", platform="tiktok")

        # Upload video bytes
        async with httpx.AsyncClient(timeout=300) as client:
            with open(video, "rb") as f:
                resp = await client.put(
                    upload_url,
                    content=f.read(),
                    headers={
                        "Content-Type": "video/mp4",
                        "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
                    },
                )
                if resp.status_code >= 400:
                    raise PublishError(
                        f"Video upload failed: {resp.status_code}",
                        platform="tiktok",
                    )

        return publish_id

    async def get_post_status(self, publish_id: str) -> dict:
        """Check the publish status of a video."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TT_API}/post/publish/status/fetch/",
                headers=self._headers(),
                json={"publish_id": publish_id},
            )
            return self._check_response(resp, "status_check")

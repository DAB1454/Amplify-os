"""Publish videos to TikTok via the Content Posting API v2.

Two-phase upload flow:
1. Initialize upload → get upload_url
2. Upload video bytes to upload_url
3. Optionally publish or save as draft
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from amplify.adapters.base import PublishError, RateLimitError, ValidationError

logger = logging.getLogger(__name__)

TT_API = "https://open.tiktokapis.com/v2"
MAX_VIDEO_SIZE = 4 * 1024 * 1024 * 1024  # 4 GB
CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB per chunk


def _is_url(path: str) -> bool:
    """Check if a string looks like a URL."""
    try:
        parsed = urlparse(str(path))
        return parsed.scheme in ("http", "https")
    except Exception:
        return False


async def _download_video(url: str) -> Path:
    """Download a video URL to a temporary file. Returns the temp file path."""
    logger.info("Downloading video from URL: %s", url)
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        resp = await client.get(url)
        if resp.status_code >= 400:
            raise ValidationError(
                f"Failed to download video from {url} (HTTP {resp.status_code})",
                platform="tiktok",
            )
        content_type = resp.headers.get("content-type", "")
        if resp.num_bytes_downloaded == 0 and len(resp.content) == 0:
            raise ValidationError(
                f"Downloaded empty file from {url}",
                platform="tiktok",
            )
        suffix = ".mp4"
        if "webm" in content_type:
            suffix = ".webm"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(resp.content)
        tmp.close()
        logger.info("Downloaded %d bytes to %s", len(resp.content), tmp.name)
        return Path(tmp.name)


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
        """Initialize a video upload and get the upload URL.

        Uses the direct publish endpoint (requires video.publish scope)
        so the caption and privacy settings are applied immediately.
        """
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TT_API}/post/publish/video/init/",
                headers=self._headers(),
                json={
                    "post_info": post_info,
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": video_size,
                        "chunk_size": min(video_size, CHUNK_SIZE),
                        "total_chunk_count": max(1, -(-video_size // CHUNK_SIZE)),
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
        privacy_level: str = "PUBLIC_TO_EVERYONE",
        as_draft: bool = False,
    ) -> str:
        """Upload a video to TikTok. Returns the publish_id.

        video_path can be a local file path or an HTTP(S) URL.
        URLs are downloaded to a temp file before uploading.
        """
        downloaded_tmp: Path | None = None
        if _is_url(str(video_path)):
            downloaded_tmp = await _download_video(str(video_path))
            video = downloaded_tmp
        else:
            video = Path(video_path)

        try:
            return await self._upload_from_file(
                video, caption,
                privacy_level=privacy_level, as_draft=as_draft,
            )
        finally:
            if downloaded_tmp is not None:
                try:
                    downloaded_tmp.unlink()
                except OSError:
                    pass

    async def _upload_from_file(
        self,
        video: Path,
        caption: str,
        *,
        privacy_level: str = "PUBLIC_TO_EVERYONE",
        as_draft: bool = False,
    ) -> str:
        """Upload a local video file to TikTok. Returns the publish_id."""
        if not video.exists():
            raise ValidationError(f"Video not found: {video}", platform="tiktok")

        file_size = video.stat().st_size
        if file_size > MAX_VIDEO_SIZE:
            raise ValidationError(
                f"Video too large ({file_size} bytes, max {MAX_VIDEO_SIZE})",
                platform="tiktok",
            )

        post_info: dict[str, Any] = {
            "title": caption[:2200],
            "privacy_level": privacy_level,
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        }

        logger.info("TikTok publish — caption (%d chars): %s", len(caption), caption[:100])

        # Init upload
        init_data = await self._init_upload(file_size, post_info=post_info)
        publish_id = init_data.get("data", {}).get("publish_id", "")
        upload_url = init_data.get("data", {}).get("upload_url", "")

        if not upload_url:
            raise PublishError("No upload URL returned", platform="tiktok")

        # Upload video bytes in chunks
        async with httpx.AsyncClient(timeout=300) as client:
            with open(video, "rb") as f:
                offset = 0
                while offset < file_size:
                    chunk = f.read(CHUNK_SIZE)
                    chunk_end = offset + len(chunk) - 1
                    resp = await client.put(
                        upload_url,
                        content=chunk,
                        headers={
                            "Content-Type": "video/mp4",
                            "Content-Range": f"bytes {offset}-{chunk_end}/{file_size}",
                        },
                    )
                    if resp.status_code >= 400:
                        raise PublishError(
                            f"Video upload failed: {resp.status_code}",
                            platform="tiktok",
                        )
                    offset += len(chunk)

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

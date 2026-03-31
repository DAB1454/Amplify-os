"""Upload videos to YouTube via the Data API v3.

Uses resumable upload for reliability on large files.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from amplify.adapters.base import PublishError, RateLimitError, ValidationError

logger = logging.getLogger(__name__)

YT_API = "https://www.googleapis.com/youtube/v3"
YT_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
MAX_VIDEO_SIZE = 256 * 1024 * 1024 * 1024  # 256 GB


def _is_url(path: str) -> bool:
    """Check if a string looks like a URL."""
    try:
        parsed = urlparse(str(path))
        return parsed.scheme in ("http", "https")
    except Exception:
        return False


async def _download_video(url: str) -> Path:
    """Download a video URL to a temporary file."""
    logger.info("Downloading video from URL: %s", url)
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        resp = await client.get(url)
        if resp.status_code >= 400:
            raise ValidationError(
                f"Failed to download video from {url} (HTTP {resp.status_code})",
                platform="youtube",
            )
        if len(resp.content) == 0:
            raise ValidationError(
                f"Downloaded empty file from {url}",
                platform="youtube",
            )
        suffix = ".mp4"
        content_type = resp.headers.get("content-type", "")
        if "webm" in content_type:
            suffix = ".webm"
        elif "quicktime" in content_type or "mov" in content_type:
            suffix = ".mov"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(resp.content)
        tmp.close()
        logger.info("Downloaded %d bytes to %s", len(resp.content), tmp.name)
        return Path(tmp.name)


class YouTubeUploader:
    """Upload and update videos on YouTube via the Data API v3."""

    def __init__(self, access_token: str) -> None:
        if not access_token:
            raise ValueError("access_token is required")
        self.access_token = access_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _check_response(self, resp: httpx.Response, operation: str) -> None:
        if resp.status_code == 403:
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            errors = body.get("error", {}).get("errors", [])
            if any(e.get("reason") == "rateLimitExceeded" for e in errors):
                raise RateLimitError(
                    "YouTube quota exceeded",
                    platform="youtube",
                )
        if resp.status_code >= 400:
            raise PublishError(
                f"YouTube {operation} failed ({resp.status_code}): {resp.text[:500]}",
                platform="youtube",
                details={"status": resp.status_code},
            )

    async def upload_video(self, video_path: str | Path, metadata: dict[str, Any]) -> str:
        """Upload a video to YouTube. Returns the video ID.

        video_path can be a local file path or an HTTP(S) URL.
        URLs are downloaded to a temp file before uploading.

        metadata keys:
            title: str (required)
            description: str
            tags: list[str]
            categoryId: str (default "10" = Music)
            privacyStatus: "private" | "unlisted" | "public"
        """
        downloaded_tmp: Path | None = None
        if _is_url(str(video_path)):
            downloaded_tmp = await _download_video(str(video_path))
            video = downloaded_tmp
        else:
            video = Path(video_path)

        try:
            return await self._upload_from_file(video, metadata)
        finally:
            if downloaded_tmp is not None:
                try:
                    downloaded_tmp.unlink()
                except OSError:
                    pass

    async def _upload_from_file(self, video: Path, metadata: dict[str, Any]) -> str:
        """Upload a local video file to YouTube. Returns the video ID."""
        if not video.exists():
            raise ValidationError(f"Video not found: {video}", platform="youtube")

        file_size = video.stat().st_size
        if file_size > MAX_VIDEO_SIZE:
            raise ValidationError(f"Video too large: {file_size} bytes", platform="youtube")

        # Build resource body — YouTube Shorts descriptions max 100 chars
        description = metadata.get("description", "")
        if len(description) > 100:
            description = description[:97] + "..."

        snippet = {
            "title": metadata.get("title", "Untitled"),
            "description": description,
            "tags": metadata.get("tags", []),
            "categoryId": metadata.get("categoryId", "10"),
        }
        status = {
            "privacyStatus": metadata.get("privacyStatus", "public"),
            "selfDeclaredMadeForKids": False,
        }
        body = json.dumps({"snippet": snippet, "status": status})

        # Step 1: Initiate resumable upload
        async with httpx.AsyncClient(timeout=600) as client:
            init_resp = await client.post(
                YT_UPLOAD,
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers={
                    **self._headers(),
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Length": str(file_size),
                    "X-Upload-Content-Type": "video/*",
                },
                content=body,
            )
            self._check_response(init_resp, "upload_init")

            upload_url = init_resp.headers.get("Location")
            if not upload_url:
                raise PublishError("No upload URL in response", platform="youtube")

            # Step 2: Upload the file
            with open(video, "rb") as f:
                upload_resp = await client.put(
                    upload_url,
                    content=f.read(),
                    headers={
                        "Content-Type": "video/*",
                        "Content-Length": str(file_size),
                    },
                )
                self._check_response(upload_resp, "upload_video")
                data = upload_resp.json()

        video_id = data.get("id", "")
        logger.info("Uploaded video %s (id=%s)", video.name, video_id)
        return video_id

    async def update_video(self, video_id: str, metadata: dict[str, Any]) -> bool:
        """Update metadata on an existing video."""
        body: dict[str, Any] = {"id": video_id}

        if any(k in metadata for k in ("title", "description", "tags", "categoryId")):
            body["snippet"] = {}
            for key in ("title", "description", "tags", "categoryId"):
                if key in metadata:
                    body["snippet"][key] = metadata[key]

        if "privacyStatus" in metadata:
            body["status"] = {"privacyStatus": metadata["privacyStatus"]}

        parts = []
        if "snippet" in body:
            parts.append("snippet")
        if "status" in body:
            parts.append("status")

        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{YT_API}/videos",
                params={"part": ",".join(parts)},
                headers={
                    **self._headers(),
                    "Content-Type": "application/json",
                },
                content=json.dumps(body),
            )
            self._check_response(resp, "update_video")

        return True

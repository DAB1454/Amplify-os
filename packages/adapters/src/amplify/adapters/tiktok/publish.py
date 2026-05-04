"""Publish videos to TikTok via the Content Posting API v2.

Two-phase upload flow:
1. Initialize upload → get upload_url
2. Upload video bytes to upload_url
3. Optionally publish or save as draft

For unaudited apps (sandbox), the inbox endpoint is used which sends the
video to the creator's TikTok app for review before publishing. Set
TIKTOK_APP_AUDITED=true once the app passes TikTok review to switch to
direct publish.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from amplify.adapters.base import PublishError, RateLimitError, TokenExpiredError, ValidationError

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
        if resp.status_code in (401, 403):
            # Check if this is an app-audit or scope issue, not a token issue
            try:
                body = resp.json()
                err_code = body.get("error", {}).get("code", "")
                if "unaudited_client" in str(err_code):
                    raise PublishError(
                        f"TikTok app not yet approved — unaudited apps can only post to private accounts. "
                        f"Check app review status at developers.tiktok.com. Raw: {resp.text[:300]}",
                        platform="tiktok",
                        permanent=True,
                        details=body.get("error", {}),
                    )
                if "scope_not_authorized" in str(err_code):
                    raise PublishError(
                        f"TikTok scope not authorized — the connected account may need to be "
                        f"reconnected with updated permissions. Disconnect and reconnect TikTok "
                        f"on the Channels page. Raw: {resp.text[:300]}",
                        platform="tiktok",
                        permanent=True,
                        details=body.get("error", {}),
                    )
            except PublishError:
                raise
            except Exception:
                pass  # JSON parse failed — fall through to token error
            raise TokenExpiredError(
                f"TikTok token expired or revoked during {operation}: {resp.text[:500]}",
                platform="tiktok",
                details={"status": resp.status_code},
            )
        if resp.status_code >= 400:
            raise PublishError(
                f"TikTok {operation} failed ({resp.status_code}): {resp.text[:500]}",
                platform="tiktok",
                details={"status": resp.status_code},
            )
        data = resp.json()
        error_info = data.get("error", {})
        error_code = error_info.get("code")
        if error_code not in (None, "ok", 0):
            error_str = str(error_code).lower()
            error_msg = str(error_info.get("message", "")).lower()
            if "scope_not_authorized" in error_str:
                raise PublishError(
                    f"TikTok scope not authorized — disconnect and reconnect TikTok "
                    f"with updated permissions. Error: {error_info}",
                    platform="tiktok",
                    permanent=True,
                    details=error_info,
                )
            if any(kw in error_str for kw in ("access_token", "token_invalid", "token_expired", "unauthorized")):
                raise TokenExpiredError(
                    f"TikTok {operation} auth error: {error_info}",
                    platform="tiktok",
                    details=error_info,
                )
            if "spam_risk_too_many_pending" in error_msg:
                raise PublishError(
                    f"TikTok has too many pending uploads — wait ~30 minutes for them to expire before retrying. Raw: {error_info}",
                    platform="tiktok",
                    permanent=True,
                    details=error_info,
                )
            raise PublishError(
                f"TikTok {operation} error: {error_info}",
                platform="tiktok",
                details=error_info,
            )
        return data

    @staticmethod
    def _is_app_audited() -> bool:
        """Check if the TikTok app has passed review (audited).

        Unaudited apps must use the inbox endpoint which sends the video
        to the creator's TikTok app for review. Audited apps can publish
        directly.
        """
        return os.environ.get("TIKTOK_APP_AUDITED", "").lower() in ("true", "1", "yes")

    async def _init_upload(
        self,
        video_size: int,
        *,
        post_info: dict[str, Any] | None = None,
    ) -> dict:
        """Initialize a video upload and get the upload URL.

        For audited apps: uses direct publish endpoint with post_info
        (caption, privacy_level, etc.).
        For unaudited apps: uses inbox endpoint with source_info only
        (creator sets caption when reviewing in TikTok app).
        """
        source_info = {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": min(video_size, CHUNK_SIZE),
            "total_chunk_count": max(1, -(-video_size // CHUNK_SIZE)),
        }

        audited = self._is_app_audited()
        logger.info("TikTok _init_upload: TIKTOK_APP_AUDITED=%s, audited=%s",
                     os.environ.get("TIKTOK_APP_AUDITED", "<not set>"), audited)

        if audited:
            endpoint = f"{TT_API}/post/publish/video/init/"
            body: dict[str, Any] = {
                "post_info": post_info or {},
                "source_info": source_info,
            }
        else:
            # Inbox endpoint does NOT accept post_info — only source_info.
            # The creator sets caption/privacy when reviewing in TikTok.
            endpoint = f"{TT_API}/post/publish/inbox/video/init/"
            body = {"source_info": source_info}
            logger.info("Using inbox endpoint (unaudited app) — creator will review in TikTok")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                endpoint,
                headers=self._headers(),
                json=body,
            )
            data = self._check_response(resp, "init_upload")
            logger.info("TikTok init_upload response: publish_id=%s", data.get("data", {}).get("publish_id", ""))
            return data

    async def upload_video(
        self,
        video_path: str | Path,
        caption: str,
        *,
        sounds: list[str] | None = None,
        privacy_level: str = "PUBLIC_TO_EVERYONE",
        as_draft: bool = False,
    ) -> dict:
        """Upload a video to TikTok.

        Returns dict with:
            publish_id: str — TikTok's identifier for this upload
            final_status: str — terminal status from TikTok (PUBLISH_COMPLETE,
                SEND_TO_USER_INBOX, FAILED, or a non-terminal PROCESSING_*
                state if polling timed out)
            fail_reason: str | None — TikTok's reason if status == FAILED

        Raises PublishError(permanent=True) if TikTok terminal state is FAILED.

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
    ) -> dict:
        """Upload a local video file to TikTok. See upload_video for return shape."""
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

        logger.info("TikTok publish — file_size=%d, caption (%d chars): %s", file_size, len(caption), caption[:100])

        # Init upload
        init_data = await self._init_upload(file_size, post_info=post_info)
        publish_id = init_data.get("data", {}).get("publish_id", "")
        upload_url = init_data.get("data", {}).get("upload_url", "")

        logger.info("TikTok init_upload OK — publish_id=%s, has_upload_url=%s", publish_id, bool(upload_url))

        if not upload_url:
            raise PublishError("No upload URL returned", platform="tiktok")

        # Upload video bytes in chunks
        chunk_num = 0
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
                    chunk_num += 1
                    logger.info(
                        "TikTok chunk %d upload: bytes %d-%d/%d → HTTP %d",
                        chunk_num, offset, chunk_end, file_size, resp.status_code,
                    )
                    if resp.status_code >= 400:
                        raise PublishError(
                            f"Video chunk upload failed: HTTP {resp.status_code} body={resp.text[:300]}",
                            platform="tiktok",
                        )
                    offset += len(chunk)

        logger.info("TikTok video upload complete — %d chunks, publish_id=%s", chunk_num, publish_id)

        # Poll TikTok status endpoint until terminal state. Without this, a
        # silent post-processing rejection (e.g. unaudited Direct Post quota,
        # content policy fail) is only logged as a warning and the post is
        # marked "published" while the video is nowhere on TikTok.
        poll = await self._poll_publish_status(publish_id)
        final_status = poll["status"]
        fail_reason = poll["fail_reason"]

        if final_status == "FAILED":
            raise PublishError(
                f"TikTok rejected video after upload: {fail_reason or 'no reason provided'} "
                f"(publish_id={publish_id})",
                platform="tiktok",
                permanent=True,
                details={
                    "publish_id": publish_id,
                    "fail_reason": fail_reason,
                    "raw": poll.get("raw"),
                },
            )

        return {
            "publish_id": publish_id,
            "final_status": final_status,
            "fail_reason": fail_reason,
        }

    async def _poll_publish_status(
        self,
        publish_id: str,
        *,
        timeout_seconds: int = 60,
        initial_delay: int = 2,
        poll_interval: int = 3,
    ) -> dict:
        """Poll TikTok's status endpoint until terminal state or timeout.

        Terminal states: PUBLISH_COMPLETE, SEND_TO_USER_INBOX, FAILED.
        On timeout, returns the last observed (non-terminal) status so the
        caller can decide what to record. Never raises — caller inspects
        the returned status field.
        """
        import asyncio

        terminal = {"PUBLISH_COMPLETE", "SEND_TO_USER_INBOX", "FAILED"}
        elapsed = 0
        last_status: str | None = None
        last_data: dict | None = None
        last_fail_reason: str | None = None

        await asyncio.sleep(initial_delay)
        elapsed += initial_delay

        while elapsed <= timeout_seconds:
            try:
                data = await self.get_post_status(publish_id)
            except Exception as exc:
                logger.warning(
                    "TikTok status poll failed at %ds for publish_id=%s: %s",
                    elapsed, publish_id, exc,
                )
                await asyncio.sleep(poll_interval)
                elapsed += poll_interval
                continue

            inner = data.get("data", {})
            last_status = inner.get("status")
            last_data = data
            last_fail_reason = inner.get("fail_reason")

            logger.info(
                "TikTok publish_id=%s status=%s elapsed=%ds fail_reason=%s",
                publish_id, last_status, elapsed, last_fail_reason,
            )

            if last_status in terminal:
                return {
                    "status": last_status,
                    "fail_reason": last_fail_reason,
                    "raw": last_data,
                }

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        logger.warning(
            "TikTok publish_id=%s did not reach terminal state in %ds (last=%s)",
            publish_id, timeout_seconds, last_status,
        )
        return {
            "status": last_status or "TIMEOUT",
            "fail_reason": last_fail_reason,
            "raw": last_data,
        }

    async def get_post_status(self, publish_id: str) -> dict:
        """Check the publish status of a video."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TT_API}/post/publish/status/fetch/",
                headers=self._headers(),
                json={"publish_id": publish_id},
            )
            return self._check_response(resp, "status_check")

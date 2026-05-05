"""TikTok analytics and metrics via the Research API."""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from amplify.adapters.base import (
    FetchError,
    InsufficientScopeError,
    MetricSnapshot,
    RateLimitError,
    TokenExpiredError,
)


def _classify_auth_error(text: str, platform: str = "tiktok") -> Exception:
    """Map a 401/403 response body to the right exception type.

    scope_not_authorized means the token is fine but missing the scope —
    don't surface this as "token expired" because (a) refresh won't help
    and (b) it confuses both logs and any downstream retry logic. Returns
    an InsufficientScopeError when the body indicates a scope problem,
    otherwise a TokenExpiredError.
    """
    snippet = (text or "").lower()
    if "scope_not_authorized" in snippet:
        return InsufficientScopeError(
            f"TikTok token missing required scope (likely video.list). "
            f"Disconnect and reconnect the channel to re-authorize. Raw: {text[:300]}",
            platform=platform,
            missing_scope="video.list",
        )
    return TokenExpiredError(
        f"TikTok token expired or revoked: {text[:500]}",
        platform=platform,
    )

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
            if resp.status_code in (401, 403):
                raise _classify_auth_error(resp.text, "tiktok")
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
            if resp.status_code in (401, 403):
                raise _classify_auth_error(resp.text, "tiktok")
            # TikTok also wraps scope errors as 200 with error.code in some flows
            try:
                _body = resp.json()
                _err = (_body.get("error") or {}).get("code", "")
                if isinstance(_err, str) and "scope_not_authorized" in _err.lower():
                    raise _classify_auth_error(resp.text, "tiktok")
            except InsufficientScopeError:
                raise
            except Exception:
                pass
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

"""YouTube comment management via the Data API v3."""

from __future__ import annotations

import logging
from datetime import datetime

import httpx

from amplify.adapters.base import Comment, FetchError, PublishError, RateLimitError

logger = logging.getLogger(__name__)

YT_API = "https://www.googleapis.com/youtube/v3"


class YouTubeComments:
    """Read, reply to, and moderate YouTube comments."""

    def __init__(self, access_token: str) -> None:
        if not access_token:
            raise ValueError("access_token is required")
        self.access_token = access_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _check(self, resp: httpx.Response, op: str) -> None:
        if resp.status_code == 403:
            raise RateLimitError(f"YouTube quota exceeded on {op}", platform="youtube")
        if resp.status_code >= 400:
            raise FetchError(
                f"YouTube {op} failed ({resp.status_code}): {resp.text[:500]}",
                platform="youtube",
            )

    async def get_comments(self, video_id: str, limit: int = 50) -> list[Comment]:
        """Fetch top-level comments on a video."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{YT_API}/commentThreads",
                headers=self._headers(),
                params={
                    "part": "snippet",
                    "videoId": video_id,
                    "maxResults": str(min(limit, 100)),
                    "order": "time",
                },
            )
            self._check(resp, "get_comments")
            data = resp.json()

        comments = []
        for item in data.get("items", []):
            snippet = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            published = snippet.get("publishedAt")
            comments.append(Comment(
                platform="youtube",
                comment_id=item.get("id", ""),
                post_id=video_id,
                author=snippet.get("authorDisplayName", ""),
                text=snippet.get("textDisplay", ""),
                like_count=snippet.get("likeCount", 0),
                reply_count=item.get("snippet", {}).get("totalReplyCount", 0),
                created_at=datetime.fromisoformat(published.replace("Z", "+00:00")) if published else None,
                raw=item,
            ))
        return comments

    async def reply(self, comment_id: str, text: str) -> str:
        """Reply to a comment. Returns the new comment ID."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{YT_API}/comments",
                headers={
                    **self._headers(),
                    "Content-Type": "application/json",
                },
                params={"part": "snippet"},
                json={
                    "snippet": {
                        "parentId": comment_id,
                        "textOriginal": text,
                    },
                },
            )
            if resp.status_code >= 400:
                raise PublishError(
                    f"Reply failed: {resp.text[:500]}",
                    platform="youtube",
                )
            data = resp.json()
        return data.get("id", "")

    async def moderate(self, comment_id: str, action: str) -> bool:
        """Moderate a comment: 'published', 'heldForReview', 'rejected'."""
        valid_actions = {"published", "heldForReview", "rejected"}
        if action not in valid_actions:
            raise ValueError(f"Invalid action '{action}', must be one of {valid_actions}")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{YT_API}/comments/setModerationStatus",
                headers=self._headers(),
                params={
                    "id": comment_id,
                    "moderationStatus": action,
                },
            )
            if resp.status_code >= 400:
                raise PublishError(
                    f"Moderation failed: {resp.text[:500]}",
                    platform="youtube",
                )
        return True

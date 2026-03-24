"""Instagram comment management — read, reply, moderate."""

from __future__ import annotations

import logging

import httpx

from amplify.adapters.base import FetchError, PublishError, RateLimitError, Comment

logger = logging.getLogger(__name__)

GRAPH_API = "https://graph.instagram.com/v21.0"


class InstagramComments:
    """Read, reply to, and moderate Instagram comments."""

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
                    f"Instagram API error: {resp.text[:500]}",
                    platform="instagram",
                )
            return resp.json()

    async def _post(self, endpoint: str, data: dict) -> dict:
        data["access_token"] = self.access_token
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{GRAPH_API}{endpoint}", data=data)
            if resp.status_code >= 400:
                raise PublishError(
                    f"Instagram API error: {resp.text[:500]}",
                    platform="instagram",
                )
            return resp.json()

    async def get_comments(self, media_id: str, limit: int = 50) -> list[Comment]:
        """Fetch comments on a media post."""
        data = await self._get(
            f"/{media_id}/comments",
            params={
                "fields": "id,text,username,timestamp,like_count,replies{id,text,username,timestamp}",
                "limit": str(limit),
            },
        )

        comments = []
        for c in data.get("data", []):
            replies = c.get("replies", {}).get("data", [])
            comments.append(Comment(
                platform="instagram",
                comment_id=c["id"],
                post_id=media_id,
                author=c.get("username", ""),
                text=c.get("text", ""),
                like_count=c.get("like_count", 0),
                reply_count=len(replies),
                raw=c,
            ))
        return comments

    async def reply(self, comment_id: str, text: str) -> str:
        """Reply to a comment. Returns the new comment ID."""
        result = await self._post(
            f"/{comment_id}/replies",
            {"message": text},
        )
        return result["id"]

    async def hide(self, comment_id: str) -> bool:
        """Hide a comment. Returns True on success."""
        await self._post(f"/{comment_id}", {"hide": "true"})
        return True

    async def unhide(self, comment_id: str) -> bool:
        """Unhide a previously hidden comment."""
        await self._post(f"/{comment_id}", {"hide": "false"})
        return True

    async def delete(self, comment_id: str) -> bool:
        """Delete a comment (only works on own comments/replies)."""
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{GRAPH_API}/{comment_id}",
                params={"access_token": self.access_token},
            )
            if resp.status_code >= 400:
                raise PublishError(
                    f"Delete failed: {resp.text[:500]}",
                    platform="instagram",
                )
        return True

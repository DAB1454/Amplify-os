"""Linktree assisted adapter — manual sync with tracked link support.

Linktree has a limited API. This adapter models the connection
for future API integration while supporting manual-sync task flows,
tracked link generation, and verification checks today.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from amplify.adapters.base import (
    BaseAdapter,
    Comment,
    ConnectionHealth,
    ConnectionStatus,
    FetchedPost,
    MetricSnapshot,
    PublishError,
    PublishResult,
)

LINKTREE_URL_PATTERN = re.compile(
    r"^https?://linktr\.ee/[\w.-]+$"
)


class LinktreeAdapter(BaseAdapter):
    """Assisted adapter for Linktree.

    Provides:
    - Connection modelling (store Linktree profile URL)
    - URL validation and verification
    - Tracked link generation with UTM parameters
    - Manual-sync task workflow support
    """

    platform = "linktree"

    def __init__(self, *, dry_run: bool = False) -> None:
        super().__init__(dry_run=dry_run)
        self._linktree_url: str = ""
        self._username: str = ""

    async def connect(self, credentials: dict[str, Any]) -> ConnectionHealth:
        """Connect with Linktree profile metadata.

        Credentials dict:
            linktree_url: str (required) — e.g. https://linktr.ee/artistname
            access_token: str (optional — for future API integration)
        """
        self._linktree_url = credentials.get("linktree_url", "")
        self._access_token = credentials.get("access_token", "")

        if not self._linktree_url:
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.ERROR,
                error="linktree_url is required",
            )

        if not LINKTREE_URL_PATTERN.match(self._linktree_url):
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.ERROR,
                error="Invalid Linktree URL — expected https://linktr.ee/<username>",
            )

        parsed = urlparse(self._linktree_url)
        self._username = parsed.path.strip("/")
        self._display_name = self._username
        self._connected = True

        return await self.validate_connection()

    async def validate_connection(self) -> ConnectionHealth:
        """Validate by checking the Linktree profile is reachable."""
        if not self._linktree_url:
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.ERROR,
                error="No Linktree URL configured",
            )

        if self.dry_run:
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.CONNECTED,
                display_name=self._display_name or "[dry_run]",
                account_id=self._linktree_url,
            )

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
                resp = await client.head(self._linktree_url)
                if resp.status_code < 400:
                    return ConnectionHealth(
                        platform=self.platform,
                        status=ConnectionStatus.CONNECTED,
                        account_id=self._linktree_url,
                        display_name=self._display_name,
                    )
                return ConnectionHealth(
                    platform=self.platform,
                    status=ConnectionStatus.ERROR,
                    error=f"Linktree profile returned {resp.status_code}",
                )
        except httpx.HTTPError as e:
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.ERROR,
                error=str(e),
            )

    def generate_tracked_link(
        self,
        target_url: str,
        *,
        campaign: str = "",
        source: str = "linktree",
        medium: str = "social",
        content: str = "",
    ) -> str:
        """Generate a URL with UTM tracking parameters.

        Returns the target_url with appended UTM query parameters.
        """
        params: dict[str, str] = {
            "utm_source": source,
            "utm_medium": medium,
        }
        if campaign:
            params["utm_campaign"] = campaign
        if content:
            params["utm_content"] = content

        separator = "&" if "?" in target_url else "?"
        return f"{target_url}{separator}{urlencode(params)}"

    async def verify_links(self, urls: list[str]) -> list[dict]:
        """Check multiple URLs for reachability.

        Returns list of dicts with: url, is_valid, status_code, error.
        """
        results = []
        for url in urls:
            if self.dry_run:
                results.append({
                    "url": url,
                    "is_valid": True,
                    "status_code": 200,
                    "error": None,
                })
                continue

            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
                    resp = await client.head(url)
                    results.append({
                        "url": url,
                        "is_valid": resp.status_code < 400,
                        "status_code": resp.status_code,
                        "error": None if resp.status_code < 400 else f"HTTP {resp.status_code}",
                    })
            except httpx.HTTPError as e:
                results.append({
                    "url": url,
                    "is_valid": False,
                    "status_code": None,
                    "error": str(e),
                })

        return results

    # ── PlatformAdapter stubs (not supported for assisted) ────────

    async def publish(
        self,
        content: str,
        media_paths: list[str] | None = None,
        **kwargs: Any,
    ) -> PublishResult:
        """Linktree does not support auto-publish."""
        raise PublishError(
            "Linktree is an assisted integration — use the task workflow to sync manually",
            platform=self.platform,
        )

    async def fetch_post(self, platform_post_id: str) -> FetchedPost:
        raise PublishError(
            "Linktree does not have a post-fetch API",
            platform=self.platform,
        )

    async def fetch_comments(
        self, platform_post_id: str, limit: int = 50
    ) -> list[Comment]:
        return []

    async def sync_metrics(self, platform_post_id: str) -> MetricSnapshot:
        return MetricSnapshot(platform=self.platform, post_id=platform_post_id)

"""Bandcamp assisted adapter — task-based manual workflows.

Bandcamp has no public publishing API, so this adapter models
the connection as metadata-only and surfaces manual checklists
instead of auto-publish flows.
"""

from __future__ import annotations

import re
from typing import Any

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
    ValidationError,
)

BANDCAMP_URL_PATTERN = re.compile(
    r"^https?://[\w-]+\.bandcamp\.com(/.*)?$"
)


class BandcampAdapter(BaseAdapter):
    """Assisted adapter for Bandcamp.

    This adapter does NOT auto-publish. It provides:
    - Connection modelling (store Bandcamp URL and metadata)
    - URL validation (check that a Bandcamp page is reachable)
    - Metadata helpers for the task-based workflow
    """

    platform = "bandcamp"

    def __init__(self, *, dry_run: bool = False) -> None:
        super().__init__(dry_run=dry_run)
        self._bandcamp_url: str = ""
        self._artist_name: str = ""

    async def connect(self, credentials: dict[str, Any]) -> ConnectionHealth:
        """Connect with Bandcamp metadata (URL, artist name).

        Credentials dict:
            bandcamp_url: str (required) — e.g. https://artist.bandcamp.com
            artist_name: str (optional)
        """
        self._bandcamp_url = credentials.get("bandcamp_url", "")
        self._artist_name = credentials.get("artist_name", "")
        self._display_name = self._artist_name or self._bandcamp_url

        if not self._bandcamp_url:
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.ERROR,
                error="bandcamp_url is required",
            )

        if not BANDCAMP_URL_PATTERN.match(self._bandcamp_url):
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.ERROR,
                error="Invalid Bandcamp URL format — expected https://<artist>.bandcamp.com",
            )

        self._connected = True
        return await self.validate_connection()

    async def validate_connection(self) -> ConnectionHealth:
        """Validate by checking the Bandcamp URL is reachable."""
        if not self._bandcamp_url:
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.ERROR,
                error="No Bandcamp URL configured",
            )

        if self.dry_run:
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.CONNECTED,
                display_name=self._display_name or "[dry_run]",
                account_id=self._bandcamp_url,
            )

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
                resp = await client.head(self._bandcamp_url)
                if resp.status_code < 400:
                    return ConnectionHealth(
                        platform=self.platform,
                        status=ConnectionStatus.CONNECTED,
                        account_id=self._bandcamp_url,
                        display_name=self._display_name,
                    )
                return ConnectionHealth(
                    platform=self.platform,
                    status=ConnectionStatus.ERROR,
                    error=f"Bandcamp URL returned {resp.status_code}",
                )
        except httpx.HTTPError as e:
            return ConnectionHealth(
                platform=self.platform,
                status=ConnectionStatus.ERROR,
                error=str(e),
            )

    async def validate_url(self, url: str) -> dict:
        """Validate a specific Bandcamp URL is reachable and extract page title.

        Returns dict with keys: url, is_valid, status_code, title, error.
        """
        if not BANDCAMP_URL_PATTERN.match(url):
            return {
                "url": url,
                "is_valid": False,
                "status_code": None,
                "title": None,
                "error": "URL does not match Bandcamp pattern",
            }

        if self.dry_run:
            return {
                "url": url,
                "is_valid": True,
                "status_code": 200,
                "title": "[dry_run] Release Title",
                "error": None,
            }

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
                resp = await client.get(url)
                title = None
                if resp.status_code < 400:
                    match = re.search(r"<title>(.*?)</title>", resp.text, re.IGNORECASE)
                    if match:
                        title = match.group(1).strip()
                return {
                    "url": url,
                    "is_valid": resp.status_code < 400,
                    "status_code": resp.status_code,
                    "title": title,
                    "error": None if resp.status_code < 400 else f"HTTP {resp.status_code}",
                }
        except httpx.HTTPError as e:
            return {
                "url": url,
                "is_valid": False,
                "status_code": None,
                "title": None,
                "error": str(e),
            }

    # ── PlatformAdapter stubs (not supported for assisted) ────────

    async def publish(
        self,
        content: str,
        media_paths: list[str] | None = None,
        **kwargs: Any,
    ) -> PublishResult:
        """Bandcamp does not support auto-publish."""
        raise PublishError(
            "Bandcamp is an assisted integration — use the task workflow to publish manually",
            platform=self.platform,
        )

    async def fetch_post(self, platform_post_id: str) -> FetchedPost:
        """Not supported — Bandcamp has no post-fetch API."""
        raise PublishError(
            "Bandcamp does not have a post-fetch API — use URL validation instead",
            platform=self.platform,
        )

    async def fetch_comments(
        self, platform_post_id: str, limit: int = 50
    ) -> list[Comment]:
        return []

    async def sync_metrics(self, platform_post_id: str) -> MetricSnapshot:
        """Not supported — Bandcamp does not expose metrics via API."""
        return MetricSnapshot(platform=self.platform, post_id=platform_post_id)

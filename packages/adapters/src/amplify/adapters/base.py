"""Shared adapter protocol, base class, error types, and data models.

Every platform adapter implements PlatformAdapter. The base class
provides dry-run support, structured logging, and health checks.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


# ── Error hierarchy ──────────────────────────────────────────────


class AdapterError(Exception):
    """Base error for all adapter operations."""

    def __init__(self, message: str, platform: str = "", details: dict | None = None) -> None:
        super().__init__(message)
        self.platform = platform
        self.details = details or {}


class AuthenticationError(AdapterError):
    """Credentials are missing, expired, or revoked."""


class TokenExpiredError(AuthenticationError):
    """Access token has expired and needs refresh."""


class TokenRefreshError(AuthenticationError):
    """Refresh token exchange failed — user must re-authorize."""


class RateLimitError(AdapterError):
    """Platform rate limit exceeded."""

    def __init__(
        self,
        message: str,
        platform: str = "",
        retry_after: int | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message, platform, details)
        self.retry_after = retry_after


class PublishError(AdapterError):
    """Publishing content to the platform failed."""


class FetchError(AdapterError):
    """Fetching data from the platform failed."""


class ValidationError(AdapterError):
    """Input validation failed before calling the platform API."""


# ── Data models ──────────────────────────────────────────────────


class ConnectionStatus(str, Enum):
    CONNECTED = "connected"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ERROR = "error"
    UNKNOWN = "unknown"


class ConnectionHealth(BaseModel):
    """Result of a connection health check."""

    platform: str
    status: ConnectionStatus
    account_id: str = ""
    display_name: str = ""
    scopes: list[str] = Field(default_factory=list)
    token_expires_at: datetime | None = None
    error: str | None = None
    checked_at: datetime = Field(default_factory=datetime.utcnow)


class PublishResult(BaseModel):
    """Standardized result from a publish operation."""

    platform: str
    platform_post_id: str = ""
    url: str = ""
    status: str = "published"  # published, draft, processing, failed
    dry_run: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class FetchedPost(BaseModel):
    """Standardized representation of a fetched post."""

    platform: str
    platform_post_id: str
    content_text: str = ""
    media_urls: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    engagement: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


class Comment(BaseModel):
    """Standardized comment from any platform."""

    platform: str
    comment_id: str
    post_id: str = ""
    author: str = ""
    text: str = ""
    created_at: datetime | None = None
    reply_count: int = 0
    like_count: int = 0
    is_reply: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


class MetricSnapshot(BaseModel):
    """Standardized metrics from a sync operation."""

    platform: str
    post_id: str = ""
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    impressions: int = 0
    reach: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    views: int = 0
    clicks: int = 0
    followers_gained: int = 0
    extra: dict[str, Any] = Field(default_factory=dict)


# ── Protocol ─────────────────────────────────────────────────────


@runtime_checkable
class PlatformAdapter(Protocol):
    """Interface every platform adapter must implement."""

    platform: str

    async def connect(self, credentials: dict[str, Any]) -> ConnectionHealth:
        """Initialize the adapter with credentials and validate the connection."""
        ...

    async def validate_connection(self) -> ConnectionHealth:
        """Check if the current connection is still valid."""
        ...

    async def publish(
        self,
        content: str,
        media_paths: list[str] | None = None,
        **kwargs: Any,
    ) -> PublishResult:
        """Publish content to the platform."""
        ...

    async def fetch_post(self, platform_post_id: str) -> FetchedPost:
        """Fetch a single post by its platform-native ID."""
        ...

    async def fetch_comments(
        self,
        platform_post_id: str,
        limit: int = 50,
    ) -> list[Comment]:
        """Fetch comments on a post."""
        ...

    async def sync_metrics(
        self,
        platform_post_id: str,
    ) -> MetricSnapshot:
        """Fetch current engagement metrics for a post."""
        ...


# ── Base class ───────────────────────────────────────────────────


class BaseAdapter(ABC):
    """Shared base for all platform adapters.

    Provides:
    - dry_run mode (logs operations without calling APIs)
    - structured logger per adapter
    - health check caching
    - credential validation helpers
    """

    platform: str = ""

    def __init__(self, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.logger = logging.getLogger(f"adapters.{self.platform}")
        self._access_token: str = ""
        self._refresh_token: str = ""
        self._token_expires_at: datetime | None = None
        self._account_id: str = ""
        self._display_name: str = ""
        self._connected: bool = False

    # ── Credential helpers ───────────────────────────────────────

    def _require_connected(self) -> None:
        """Raise AuthenticationError if not connected."""
        if not self._connected:
            raise AuthenticationError(
                "Adapter not connected — call connect() first",
                platform=self.platform,
            )

    def _require_token(self) -> None:
        """Raise AuthenticationError if access token is missing."""
        if not self._access_token:
            raise AuthenticationError(
                "No access token — call connect() first",
                platform=self.platform,
            )

    def _is_token_expired(self) -> bool:
        if self._token_expires_at is None:
            return False
        return datetime.utcnow() >= self._token_expires_at

    def _check_token_expiry(self) -> None:
        """Raise TokenExpiredError if the access token has expired."""
        if self._is_token_expired():
            raise TokenExpiredError(
                "Access token has expired — refresh required",
                platform=self.platform,
            )

    # ── Dry-run helper ───────────────────────────────────────────

    def _dry_result(self, operation: str, **meta: Any) -> PublishResult:
        """Return a PublishResult for dry-run mode."""
        self.logger.info("[DRY RUN] %s: %s", operation, meta)
        return PublishResult(
            platform=self.platform,
            platform_post_id=f"dry_run_{operation}",
            status="dry_run",
            dry_run=True,
            metadata={"operation": operation, **meta},
        )

    # ── Abstract methods ─────────────────────────────────────────

    @abstractmethod
    async def connect(self, credentials: dict[str, Any]) -> ConnectionHealth:
        ...

    @abstractmethod
    async def validate_connection(self) -> ConnectionHealth:
        ...

    @abstractmethod
    async def publish(
        self,
        content: str,
        media_paths: list[str] | None = None,
        **kwargs: Any,
    ) -> PublishResult:
        ...

    @abstractmethod
    async def fetch_post(self, platform_post_id: str) -> FetchedPost:
        ...

    @abstractmethod
    async def fetch_comments(
        self,
        platform_post_id: str,
        limit: int = 50,
    ) -> list[Comment]:
        ...

    @abstractmethod
    async def sync_metrics(
        self,
        platform_post_id: str,
    ) -> MetricSnapshot:
        ...

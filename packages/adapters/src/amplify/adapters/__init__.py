"""Amplify-OS platform adapters."""

from amplify.adapters.base import (
    AdapterError,
    AuthenticationError,
    BaseAdapter,
    Comment,
    ConnectionHealth,
    ConnectionStatus,
    FetchedPost,
    FetchError,
    MetricSnapshot,
    PlatformAdapter,
    PublishError,
    PublishResult,
    RateLimitError,
    TokenExpiredError,
    TokenRefreshError,
    ValidationError,
)
from amplify.adapters.token_store import TokenSet, TokenStore

__all__ = [
    # Protocol & base
    "PlatformAdapter",
    "BaseAdapter",
    # Errors
    "AdapterError",
    "AuthenticationError",
    "TokenExpiredError",
    "TokenRefreshError",
    "RateLimitError",
    "PublishError",
    "FetchError",
    "ValidationError",
    # Data models
    "ConnectionHealth",
    "ConnectionStatus",
    "PublishResult",
    "FetchedPost",
    "Comment",
    "MetricSnapshot",
    # Token store
    "TokenSet",
    "TokenStore",
]

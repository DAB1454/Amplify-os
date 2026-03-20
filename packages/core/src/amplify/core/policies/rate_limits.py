"""Platform-specific rate limits."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlatformRateLimits:
    """Rate limits for a specific platform."""

    platform: str
    posts_per_day: int
    comments_per_hour: int | None = None
    uploads_per_day: int | None = None
    comments_per_minute: int | None = None


_LIMITS: dict[str, PlatformRateLimits] = {
    "instagram": PlatformRateLimits(
        platform="instagram",
        posts_per_day=25,
        comments_per_hour=200,
    ),
    "tiktok": PlatformRateLimits(
        platform="tiktok",
        posts_per_day=10,
    ),
    "youtube": PlatformRateLimits(
        platform="youtube",
        posts_per_day=6,
        uploads_per_day=6,
        comments_per_minute=50,
    ),
}


def get_rate_limit(platform: str) -> PlatformRateLimits:
    """Get rate limits for a given platform.

    Args:
        platform: Platform name (case-insensitive).

    Returns:
        PlatformRateLimits for the platform, or a permissive default.
    """
    key = platform.lower()
    if key in _LIMITS:
        return _LIMITS[key]
    # Default permissive limits for unknown platforms
    return PlatformRateLimits(platform=key, posts_per_day=50)

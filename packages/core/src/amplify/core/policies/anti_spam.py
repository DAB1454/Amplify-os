"""Anti-spam policy for limiting post frequency."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID


class AntiSpamPolicy:
    """Enforces posting rate limits to prevent spamming platforms."""

    MAX_POSTS_PER_HOUR = 3
    MAX_POSTS_PER_DAY = 12
    MIN_POST_INTERVAL_MINUTES = 15

    @classmethod
    def check_rate_limit(
        cls,
        tenant_id: UUID,
        platform: str,
        recent_posts: list[dict],
        *,
        now: datetime | None = None,
    ) -> tuple[bool, str]:
        """Check whether a new post is allowed based on recent posting history.

        Args:
            tenant_id: The tenant attempting to post.
            platform: The target platform.
            recent_posts: List of dicts with at least a "published_at" or
                "scheduled_at" key (datetime).
            now: Current time override (defaults to utcnow).

        Returns:
            (allowed, reason) — True if the post is allowed, otherwise False
            with a human-readable reason.
        """
        if now is None:
            now = datetime.utcnow()
        one_hour_ago = now - timedelta(hours=1)
        one_day_ago = now - timedelta(days=1)

        timestamps: list[datetime] = []
        for post in recent_posts:
            ts = post.get("published_at") or post.get("scheduled_at")
            if isinstance(ts, datetime):
                timestamps.append(ts)

        timestamps.sort(reverse=True)

        # Check minimum interval between posts
        if timestamps:
            minutes_since_last = (now - timestamps[0]).total_seconds() / 60
            if minutes_since_last < cls.MIN_POST_INTERVAL_MINUTES:
                remaining = cls.MIN_POST_INTERVAL_MINUTES - int(minutes_since_last)
                return (
                    False,
                    f"Too soon since last post on {platform}. "
                    f"Wait {remaining} more minute(s).",
                )

        # Check hourly limit
        posts_last_hour = sum(1 for ts in timestamps if ts >= one_hour_ago)
        if posts_last_hour >= cls.MAX_POSTS_PER_HOUR:
            return (
                False,
                f"Hourly limit reached for {platform} "
                f"({cls.MAX_POSTS_PER_HOUR} posts/hour).",
            )

        # Check daily limit
        posts_last_day = sum(1 for ts in timestamps if ts >= one_day_ago)
        if posts_last_day >= cls.MAX_POSTS_PER_DAY:
            return (
                False,
                f"Daily limit reached for {platform} "
                f"({cls.MAX_POSTS_PER_DAY} posts/day).",
            )

        return (True, "OK")

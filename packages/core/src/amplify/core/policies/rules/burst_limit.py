"""Burst posting limit rule.

Detects and blocks rapid-fire multi-post bursts within a short
window.  Different from frequency_limit — this catches clusters
of posts scheduled close together even if they don't violate the
per-hour/per-day limit individually.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from amplify.core.policies.engine import ActionContext, Decision, PolicyVerdict


class BurstLimitRule:
    """Block rapid bursts of posts within a short window."""

    name = "burst_limit"

    def __init__(
        self,
        window_minutes: int = 30,
        max_posts: int = 5,
    ) -> None:
        self.window_minutes = window_minutes
        self.max_posts = max_posts

    def evaluate(self, ctx: ActionContext) -> PolicyVerdict:
        if ctx.action_type == "reply":
            return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

        if not ctx.recent_posts:
            return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

        now = ctx.now
        window_start = now - timedelta(minutes=self.window_minutes)

        count = 0
        for post in ctx.recent_posts:
            ts = post.get("published_at") or post.get("scheduled_at")
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts)
                except ValueError:
                    continue
            if isinstance(ts, datetime) and ts >= window_start:
                count += 1

        if count >= self.max_posts:
            return PolicyVerdict(
                rule_name=self.name,
                decision=Decision.BLOCK,
                reason=(
                    f"Burst limit exceeded: {count} posts in the last "
                    f"{self.window_minutes} minutes (max {self.max_posts})."
                ),
                metadata={
                    "count": count,
                    "window_minutes": self.window_minutes,
                    "max_posts": self.max_posts,
                },
            )

        # Warn if approaching limit
        if count >= self.max_posts - 1:
            return PolicyVerdict(
                rule_name=self.name,
                decision=Decision.REQUIRE_APPROVAL,
                reason=(
                    f"Approaching burst limit: {count}/{self.max_posts} posts "
                    f"in the last {self.window_minutes} minutes."
                ),
                metadata={"count": count},
            )

        return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

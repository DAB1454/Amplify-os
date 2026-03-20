"""Posting frequency limit rule.

Blocks posts that exceed configurable per-hour and per-day limits,
or that are too close together in time.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from amplify.core.policies.engine import ActionContext, Decision, PolicyVerdict


class FrequencyLimitRule:
    """Block excessive posting frequency."""

    name = "frequency_limit"

    def __init__(
        self,
        max_per_hour: int = 3,
        max_per_day: int = 12,
        min_interval_minutes: int = 15,
    ) -> None:
        self.max_per_hour = max_per_hour
        self.max_per_day = max_per_day
        self.min_interval_minutes = min_interval_minutes

    def evaluate(self, ctx: ActionContext) -> PolicyVerdict:
        if ctx.action_type == "reply":
            return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

        if not ctx.recent_posts:
            return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

        now = ctx.now
        one_hour_ago = now - timedelta(hours=1)
        one_day_ago = now - timedelta(days=1)

        timestamps: list[datetime] = []
        for post in ctx.recent_posts:
            ts = post.get("published_at") or post.get("scheduled_at")
            if isinstance(ts, datetime):
                timestamps.append(ts)
            elif isinstance(ts, str):
                try:
                    timestamps.append(datetime.fromisoformat(ts))
                except ValueError:
                    continue

        if not timestamps:
            return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

        timestamps.sort(reverse=True)

        # Minimum interval
        minutes_since_last = (now - timestamps[0]).total_seconds() / 60
        if minutes_since_last < self.min_interval_minutes:
            remaining = self.min_interval_minutes - int(minutes_since_last)
            return PolicyVerdict(
                rule_name=self.name,
                decision=Decision.BLOCK,
                reason=(
                    f"Too soon since last post on {ctx.platform}. "
                    f"Wait {remaining} more minute(s)."
                ),
                metadata={"minutes_since_last": round(minutes_since_last, 1)},
            )

        # Hourly limit
        posts_last_hour = sum(1 for ts in timestamps if ts >= one_hour_ago)
        if posts_last_hour >= self.max_per_hour:
            return PolicyVerdict(
                rule_name=self.name,
                decision=Decision.BLOCK,
                reason=(
                    f"Hourly limit reached for {ctx.platform} "
                    f"({self.max_per_hour} posts/hour)."
                ),
                metadata={"posts_last_hour": posts_last_hour},
            )

        # Daily limit
        posts_last_day = sum(1 for ts in timestamps if ts >= one_day_ago)
        if posts_last_day >= self.max_per_day:
            return PolicyVerdict(
                rule_name=self.name,
                decision=Decision.BLOCK,
                reason=(
                    f"Daily limit reached for {ctx.platform} "
                    f"({self.max_per_day} posts/day)."
                ),
                metadata={"posts_last_day": posts_last_day},
            )

        return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

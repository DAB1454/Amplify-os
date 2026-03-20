"""Allowed hours restriction rule.

Blocks automated actions outside configurable hours.  Default
window is 06:00–23:00 to avoid posting during overnight hours
when engagement is low and automated posts look spammy.
"""

from __future__ import annotations

from amplify.core.policies.engine import ActionContext, Decision, PolicyVerdict


class HoursRestrictionRule:
    """Block actions outside allowed posting hours."""

    name = "hours_restriction"

    def __init__(self, allowed_start: int = 6, allowed_end: int = 23) -> None:
        self.allowed_start = allowed_start
        self.allowed_end = allowed_end

    def evaluate(self, ctx: ActionContext) -> PolicyVerdict:
        # Use scheduled_at if present, otherwise use now
        check_time = ctx.scheduled_at or ctx.now
        hour = check_time.hour

        if self.allowed_start <= hour < self.allowed_end:
            return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

        return PolicyVerdict(
            rule_name=self.name,
            decision=Decision.REQUIRE_APPROVAL,
            reason=(
                f"Action scheduled at {check_time.strftime('%H:%M')} is outside "
                f"allowed hours ({self.allowed_start:02d}:00–{self.allowed_end:02d}:00). "
                f"Approval required for off-hours posting."
            ),
            metadata={
                "hour": hour,
                "allowed_start": self.allowed_start,
                "allowed_end": self.allowed_end,
            },
        )

"""Missing destination link rule.

Requires that every publish action has a destination URL (link in bio,
streaming link, pre-save, etc.).  Posts without a destination are flagged
for approval.
"""

from __future__ import annotations

from amplify.core.policies.engine import ActionContext, Decision, PolicyVerdict


class MissingLinkRule:
    """Require a destination link on publish actions."""

    name = "missing_link"

    def evaluate(self, ctx: ActionContext) -> PolicyVerdict:
        if ctx.action_type not in ("publish", "schedule", ""):
            return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

        if ctx.destination_url:
            return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

        # Check if content itself contains a URL
        if ctx.content and ("http://" in ctx.content or "https://" in ctx.content):
            return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

        # Check if "link in bio" is referenced
        lower = (ctx.content or "").lower()
        if "link in bio" in lower or "linkinbio" in lower:
            return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

        return PolicyVerdict(
            rule_name=self.name,
            decision=Decision.REQUIRE_APPROVAL,
            reason="No destination link provided — every post should drive fans somewhere.",
        )

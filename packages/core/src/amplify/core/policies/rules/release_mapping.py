"""Release mapping rule.

Blocks posts that have no artist or release association.  Every
piece of content published through Amplify-OS should be tied to
an artist and ideally a campaign or release.
"""

from __future__ import annotations

from amplify.core.policies.engine import ActionContext, Decision, PolicyVerdict


class ReleaseMappingRule:
    """Require artist/release mapping on publish actions."""

    name = "release_mapping"

    def evaluate(self, ctx: ActionContext) -> PolicyVerdict:
        if ctx.action_type == "reply":
            return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

        if not ctx.artist_id:
            return PolicyVerdict(
                rule_name=self.name,
                decision=Decision.REQUIRE_APPROVAL,
                reason="Post has no artist mapping — consider linking to an artist.",
            )

        if not ctx.release_id and not ctx.campaign_id:
            return PolicyVerdict(
                rule_name=self.name,
                decision=Decision.REQUIRE_APPROVAL,
                reason=(
                    "Post is not linked to a release or campaign. "
                    "Consider associating it for tracking."
                ),
            )

        return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

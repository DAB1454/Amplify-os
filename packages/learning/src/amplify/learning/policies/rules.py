"""Learning-informed policy rules.

These rules plug into amplify.core.policies.PolicyEngine.  They consume
insights from the learning system and produce advisory verdicts.

Important: these rules NEVER block.  They produce ALLOW or
REQUIRE_APPROVAL, leaving blocking decisions to the core policy rules.
"""

from __future__ import annotations

from amplify.core.policies.engine import ActionContext, Decision, PolicyVerdict
from amplify.learning.schemas.insight import Insight, InsightType


class TimingAdvisoryRule:
    """Advises on post timing based on learned patterns.

    If the system has learned that certain hours perform poorly for this
    tenant, it can flag posts scheduled at those times for review.
    """

    name: str = "learning_timing_advisory"

    def __init__(self, insights: list[Insight] | None = None) -> None:
        self._poor_hours: set[int] = set()
        if insights:
            for insight in insights:
                if (
                    insight.insight_type == InsightType.TIMING
                    and insight.subject_feature == "post_hour"
                    and insight.confidence >= 0.7
                    and insight.subject_value is not None
                ):
                    # Insights with low reward for certain hours
                    if "low performance" in insight.explanation.lower():
                        try:
                            self._poor_hours.add(int(insight.subject_value))
                        except ValueError:
                            pass

    def evaluate(self, ctx: ActionContext) -> PolicyVerdict:
        if not ctx.scheduled_at:
            return PolicyVerdict(
                rule_name=self.name,
                decision=Decision.ALLOW,
                reason="No scheduling time to evaluate.",
            )

        hour = ctx.scheduled_at.hour
        if hour in self._poor_hours:
            return PolicyVerdict(
                rule_name=self.name,
                decision=Decision.REQUIRE_APPROVAL,
                reason=(
                    f"Hour {hour}:00 has historically low performance "
                    f"for this artist. Consider rescheduling."
                ),
                metadata={"suggested_action": "reschedule", "flagged_hour": hour},
            )

        return PolicyVerdict(
            rule_name=self.name,
            decision=Decision.ALLOW,
            reason="Scheduling time looks fine based on historical data.",
        )


class ContentTypeAdvisoryRule:
    """Advises on content type based on learned patterns.

    If the system has learned that certain content types consistently
    underperform on a platform, it flags them for review.
    """

    name: str = "learning_content_advisory"

    def __init__(self, insights: list[Insight] | None = None) -> None:
        self._weak_combos: dict[str, set[str]] = {}  # platform → {content_types}
        if insights:
            for insight in insights:
                if (
                    insight.insight_type == InsightType.CONTENT
                    and insight.subject_feature == "content_type"
                    and insight.confidence >= 0.7
                    and insight.subject_value is not None
                ):
                    if "low performance" in insight.explanation.lower():
                        platform = insight.explanation.split("on ")[-1].strip().lower() if "on " in insight.explanation else ""
                        if platform:
                            self._weak_combos.setdefault(platform, set()).add(insight.subject_value)

    def evaluate(self, ctx: ActionContext) -> PolicyVerdict:
        content_type = ctx.extra.get("content_type", "")
        weak = self._weak_combos.get(ctx.platform, set())

        if content_type and content_type in weak:
            return PolicyVerdict(
                rule_name=self.name,
                decision=Decision.REQUIRE_APPROVAL,
                reason=(
                    f"Content type '{content_type}' has historically low "
                    f"performance on {ctx.platform}. Consider an alternative."
                ),
                metadata={"content_type": content_type, "platform": ctx.platform},
            )

        return PolicyVerdict(
            rule_name=self.name,
            decision=Decision.ALLOW,
            reason="Content type looks fine based on historical data.",
        )

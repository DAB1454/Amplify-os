"""Policy engine — evaluates actions against configurable rules.

Every publish, reply, or scheduled action passes through
PolicyEngine.evaluate() before proceeding.  Each rule returns a
PolicyVerdict; the engine aggregates them into a final decision:
ALLOW, REQUIRE_APPROVAL, or BLOCK.

Rules are pluggable — register via engine.add_rule(rule).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ── Verdict model ────────────────────────────────────────────────


class Decision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    BLOCK = "block"


@dataclass
class PolicyVerdict:
    """Result of a single rule evaluation."""

    rule_name: str
    decision: Decision
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyResult:
    """Aggregated result from all rules."""

    final_decision: Decision
    verdicts: list[PolicyVerdict] = field(default_factory=list)
    evaluated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def blocked(self) -> bool:
        return self.final_decision == Decision.BLOCK

    @property
    def needs_approval(self) -> bool:
        return self.final_decision == Decision.REQUIRE_APPROVAL

    @property
    def allowed(self) -> bool:
        return self.final_decision == Decision.ALLOW

    @property
    def blocking_reasons(self) -> list[str]:
        return [v.reason for v in self.verdicts if v.decision == Decision.BLOCK]

    @property
    def approval_reasons(self) -> list[str]:
        return [v.reason for v in self.verdicts if v.decision == Decision.REQUIRE_APPROVAL]

    def summary(self) -> str:
        lines = [f"Policy decision: {self.final_decision.value}"]
        for v in self.verdicts:
            if v.decision != Decision.ALLOW:
                lines.append(f"  [{v.decision.value}] {v.rule_name}: {v.reason}")
        return "\n".join(lines)


# ── Action context ───────────────────────────────────────────────


@dataclass
class ActionContext:
    """Context passed to every rule for evaluation.

    Populate whichever fields are relevant for the action type.
    """

    action_type: str = ""  # publish, reply, schedule, burst
    platform: str = ""
    content: str = ""
    media_urls: list[str] = field(default_factory=list)

    # Mapping
    artist_id: str = ""
    release_id: str = ""
    campaign_id: str = ""
    destination_url: str = ""

    # Timing
    scheduled_at: datetime | None = None
    now: datetime = field(default_factory=datetime.utcnow)

    # History (caller supplies)
    recent_posts: list[dict[str, Any]] = field(default_factory=list)
    recent_captions: list[str] = field(default_factory=list)

    # Extras
    extra: dict[str, Any] = field(default_factory=dict)


# ── Rule protocol ────────────────────────────────────────────────


class PolicyRule(Protocol):
    """Interface for a single policy rule."""

    name: str

    def evaluate(self, ctx: ActionContext) -> PolicyVerdict:
        ...


# ── Engine ───────────────────────────────────────────────────────


class PolicyEngine:
    """Evaluates an action against all registered rules.

    The final decision is the most restrictive verdict:
    BLOCK > REQUIRE_APPROVAL > ALLOW.
    """

    def __init__(self) -> None:
        self._rules: list[PolicyRule] = []

    def add_rule(self, rule: PolicyRule) -> None:
        self._rules.append(rule)

    @property
    def rules(self) -> list[PolicyRule]:
        return list(self._rules)

    def evaluate(self, ctx: ActionContext) -> PolicyResult:
        """Run all rules and return the aggregated result."""
        verdicts: list[PolicyVerdict] = []

        for rule in self._rules:
            try:
                verdict = rule.evaluate(ctx)
                verdicts.append(verdict)
            except Exception:
                logger.exception("Rule %s raised an exception", rule.name)
                verdicts.append(PolicyVerdict(
                    rule_name=rule.name,
                    decision=Decision.BLOCK,
                    reason=f"Rule '{rule.name}' failed with an internal error",
                ))

        # Most restrictive wins
        if any(v.decision == Decision.BLOCK for v in verdicts):
            final = Decision.BLOCK
        elif any(v.decision == Decision.REQUIRE_APPROVAL for v in verdicts):
            final = Decision.REQUIRE_APPROVAL
        else:
            final = Decision.ALLOW

        result = PolicyResult(final_decision=final, verdicts=verdicts)

        if not result.allowed:
            logger.warning("Policy engine: %s", result.summary())

        return result


# ── Factory: create a fully-loaded engine ────────────────────────


def create_default_engine(
    *,
    allowed_hours: tuple[int, int] = (6, 23),
    burst_window_minutes: int = 30,
    burst_max_posts: int = 5,
    max_posts_per_hour: int = 3,
    max_posts_per_day: int = 12,
    min_post_interval_minutes: int = 15,
    duplicate_similarity_threshold: float = 0.85,
) -> PolicyEngine:
    """Create a PolicyEngine with all default rules configured."""
    from amplify.core.policies.rules.burst_limit import BurstLimitRule
    from amplify.core.policies.rules.duplicate_caption import DuplicateCaptionRule
    from amplify.core.policies.rules.frequency_limit import FrequencyLimitRule
    from amplify.core.policies.rules.hours_restriction import HoursRestrictionRule
    from amplify.core.policies.rules.missing_link import MissingLinkRule
    from amplify.core.policies.rules.release_mapping import ReleaseMappingRule
    from amplify.core.policies.rules.reply_safety import ReplySafetyRule

    engine = PolicyEngine()
    engine.add_rule(DuplicateCaptionRule(threshold=duplicate_similarity_threshold))
    engine.add_rule(FrequencyLimitRule(
        max_per_hour=max_posts_per_hour,
        max_per_day=max_posts_per_day,
        min_interval_minutes=min_post_interval_minutes,
    ))
    engine.add_rule(MissingLinkRule())
    engine.add_rule(ReleaseMappingRule())
    engine.add_rule(ReplySafetyRule())
    engine.add_rule(HoursRestrictionRule(
        allowed_start=allowed_hours[0],
        allowed_end=allowed_hours[1],
    ))
    engine.add_rule(BurstLimitRule(
        window_minutes=burst_window_minutes,
        max_posts=burst_max_posts,
    ))

    return engine

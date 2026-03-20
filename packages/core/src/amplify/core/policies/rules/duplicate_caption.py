"""Duplicate caption detection rule.

Blocks or flags posts whose caption is too similar to a recently
published caption on the same platform.  Uses simple character-level
similarity (SequenceMatcher) to avoid pulling in heavy NLP deps.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from amplify.core.policies.engine import ActionContext, Decision, PolicyVerdict


class DuplicateCaptionRule:
    """Block posts with near-duplicate captions."""

    name = "duplicate_caption"

    def __init__(self, threshold: float = 0.85) -> None:
        self.threshold = threshold

    def evaluate(self, ctx: ActionContext) -> PolicyVerdict:
        if ctx.action_type == "reply":
            return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

        if not ctx.content or not ctx.recent_captions:
            return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

        content_lower = ctx.content.strip().lower()
        if not content_lower:
            return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

        for prev in ctx.recent_captions:
            prev_lower = prev.strip().lower()
            if not prev_lower:
                continue

            ratio = SequenceMatcher(None, content_lower, prev_lower).ratio()
            if ratio >= 1.0:
                return PolicyVerdict(
                    rule_name=self.name,
                    decision=Decision.BLOCK,
                    reason=f"Exact duplicate caption detected (similarity={ratio:.0%})",
                    metadata={"similarity": ratio, "matched": prev[:80]},
                )
            if ratio >= self.threshold:
                return PolicyVerdict(
                    rule_name=self.name,
                    decision=Decision.REQUIRE_APPROVAL,
                    reason=(
                        f"Caption is very similar to a recent post "
                        f"(similarity={ratio:.0%}, threshold={self.threshold:.0%})"
                    ),
                    metadata={"similarity": ratio, "matched": prev[:80]},
                )

        return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

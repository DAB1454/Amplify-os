"""Reply safety rule.

Screens auto-generated comment replies for risky claims, spam language,
and content that could create legal or reputational risk when posted
as the artist's voice.
"""

from __future__ import annotations

import re

from amplify.core.policies.engine import ActionContext, Decision, PolicyVerdict

# Phrases that should never appear in an auto-reply
BLOCKED_PHRASES: list[str] = [
    # Legal/medical/financial claims
    "guaranteed",
    "i promise",
    "we guarantee",
    "100% certain",
    "you will definitely",
    "cure",
    "treats",
    "investment advice",
    "financial advice",
    "not financial advice",
    "legal advice",
    # Spam patterns
    "click here",
    "check my bio",
    "dm me for",
    "send me a dm",
    "follow me back",
    "follow for follow",
    "f4f",
    "s4s",
    "shoutout for shoutout",
    "check my page",
    "free giveaway",
    "you won",
    "you've been selected",
    "congratulations you",
    # Aggressive sales
    "buy now",
    "limited time only",
    "act fast",
    "don't miss out",
    "exclusive offer",
    "use my code",
    "discount code",
    "promo code",
]

# Regex patterns for risky content
RISKY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?:https?://|www\.)\S+", re.I),
        "Reply contains a URL — auto-replies should not include links",
    ),
    (
        re.compile(r"@\w+\s+(is|are)\s+(fake|trash|garbage|terrible)", re.I),
        "Reply contains a negative claim about another account",
    ),
    (
        re.compile(r"\b(hate|kill|die|threat)\b", re.I),
        "Reply contains potentially violent or threatening language",
    ),
    (
        re.compile(r"\$\d+", re.I),
        "Reply references specific dollar amounts — potential spam or scam",
    ),
]


class ReplySafetyRule:
    """Screen auto-replies for risky or spammy content."""

    name = "reply_safety"

    def evaluate(self, ctx: ActionContext) -> PolicyVerdict:
        if ctx.action_type != "reply":
            return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

        if not ctx.content:
            return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

        lower = ctx.content.lower()

        # Check blocked phrases
        for phrase in BLOCKED_PHRASES:
            if phrase.lower() in lower:
                return PolicyVerdict(
                    rule_name=self.name,
                    decision=Decision.BLOCK,
                    reason=f"Auto-reply contains blocked phrase: '{phrase}'",
                    metadata={"phrase": phrase},
                )

        # Check regex patterns
        for pattern, reason in RISKY_PATTERNS:
            if pattern.search(ctx.content):
                return PolicyVerdict(
                    rule_name=self.name,
                    decision=Decision.REQUIRE_APPROVAL,
                    reason=reason,
                    metadata={"pattern": pattern.pattern},
                )

        return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

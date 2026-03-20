"""Platform integrity policy — hard guardrails for the campaign operating system.

Amplify-OS is a campaign operating system, not a bot that chases streams.
The service we sell is:  campaign planning, asset generation, channel publishing,
approval workflows, analytics and optimization, and reusable launch templates.

This module enforces the non-negotiable lines the platform will never cross.
Every agent output and every publish action is screened through these checks
before it can proceed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class IntegrityViolation:
    """A single integrity violation."""

    rule: str
    detail: str
    severity: str = "block"  # block | warn


@dataclass
class IntegrityResult:
    """Result of an integrity check."""

    passed: bool
    violations: list[IntegrityViolation] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(v.severity == "block" for v in self.violations)


# ── Prohibited patterns ─────────────────────────────────────────
# These are matched case-insensitively against agent output, post content,
# and tool call arguments.

PROHIBITED_PHRASES: list[tuple[str, str]] = [
    # Fake engagement
    ("buy followers", "no_fake_engagement"),
    ("buy streams", "no_fake_engagement"),
    ("buy plays", "no_fake_engagement"),
    ("buy listeners", "no_fake_engagement"),
    ("buy views", "no_fake_engagement"),
    ("buy likes", "no_fake_engagement"),
    ("buy subscribers", "no_fake_engagement"),
    ("fake streams", "no_fake_engagement"),
    ("fake plays", "no_fake_engagement"),
    ("fake listeners", "no_fake_engagement"),
    ("bot streams", "no_fake_engagement"),
    ("bot plays", "no_fake_engagement"),
    ("bot farm", "no_fake_engagement"),
    ("stream farm", "no_fake_engagement"),
    ("click farm", "no_fake_engagement"),
    ("playlist stuffing", "no_fake_engagement"),
    ("guaranteed streams", "no_fake_engagement"),
    ("guaranteed plays", "no_fake_engagement"),
    ("guaranteed followers", "no_fake_engagement"),
    # Fake personas
    ("fake fan", "no_fake_personas"),
    ("fake persona", "no_fake_personas"),
    ("fake account", "no_fake_personas"),
    ("sockpuppet", "no_fake_personas"),
    ("sock puppet", "no_fake_personas"),
    ("astroturf", "no_fake_personas"),
    ("fake profile", "no_fake_personas"),
    ("fake comment", "no_fake_personas"),
    # Bulk spam
    ("mass comment", "no_bulk_spam"),
    ("bulk comment", "no_bulk_spam"),
    ("spam comment", "no_bulk_spam"),
    ("comment bomb", "no_bulk_spam"),
    ("mass DM", "no_bulk_spam"),
    ("bulk DM", "no_bulk_spam"),
    ("mass message", "no_bulk_spam"),
    ("bulk follow", "no_bulk_spam"),
    ("follow/unfollow", "no_bulk_spam"),
    ("follow unfollow", "no_bulk_spam"),
    # Impersonation
    ("impersonate", "no_impersonation"),
    ("pretend to be another artist", "no_impersonation"),
    ("pose as", "no_impersonation"),
    # Generic content dumps
    ("generic content dump", "no_generic_dumps"),
    ("mass-generated", "no_generic_dumps"),
    ("mass generated", "no_generic_dumps"),
    ("content spinning", "no_generic_dumps"),
    ("spin content", "no_generic_dumps"),
    ("article spinning", "no_generic_dumps"),
]

# Regex patterns for more nuanced detection
PROHIBITED_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"\b\d+[kK]?\s*(followers|streams|plays|views)\s*(for|at|only)\s*\$", re.I),
        "no_fake_engagement",
        "Pricing for engagement metrics suggests a pay-for-streams scheme",
    ),
    (
        re.compile(r"guarantee\w*\s+\d+.*?(stream|play|view|follower)", re.I),
        "no_fake_engagement",
        "Guaranteeing specific engagement numbers is not achievable through legitimate means",
    ),
]


# ── Rule descriptions (for audit/error messages) ────────────────

RULE_DESCRIPTIONS: dict[str, str] = {
    "no_fake_engagement": (
        "Amplify-OS does not support fake streams, plays, views, followers, "
        "or any form of artificial engagement inflation."
    ),
    "no_fake_personas": (
        "Amplify-OS does not create fake fan personas, sockpuppet accounts, "
        "or astroturfed audiences."
    ),
    "no_bulk_spam": (
        "Amplify-OS does not send bulk spam comments, mass DMs, or perform "
        "follow/unfollow manipulation."
    ),
    "no_impersonation": (
        "Amplify-OS does not impersonate other artists, labels, or public figures."
    ),
    "no_generic_dumps": (
        "Amplify-OS does not generate mass-produced generic content. Every piece "
        "of content must be tailored to the artist's voice and campaign context."
    ),
}


class IntegrityPolicy:
    """Hard guardrails for the campaign operating system.

    Every piece of content, every agent output, and every tool call argument
    is checked against these rules.  Violations with severity='block' prevent
    the action from proceeding.  There are no overrides — these rules are
    non-negotiable.
    """

    @classmethod
    def check_text(cls, text: str) -> IntegrityResult:
        """Check a piece of text against all integrity rules.

        Use this for: agent outputs, post captions, email copy, ad copy,
        campaign plan descriptions, tool call arguments.
        """
        if not text:
            return IntegrityResult(passed=True)

        violations: list[IntegrityViolation] = []
        lower = text.lower()

        # Phrase matching
        for phrase, rule in PROHIBITED_PHRASES:
            if phrase.lower() in lower:
                violations.append(IntegrityViolation(
                    rule=rule,
                    detail=f"Prohibited phrase detected: '{phrase}'",
                    severity="block",
                ))

        # Regex matching
        for pattern, rule, detail in PROHIBITED_PATTERNS:
            if pattern.search(text):
                violations.append(IntegrityViolation(
                    rule=rule,
                    detail=detail,
                    severity="block",
                ))

        # Deduplicate by rule (keep first per rule)
        seen_rules: set[str] = set()
        unique: list[IntegrityViolation] = []
        for v in violations:
            if v.rule not in seen_rules:
                seen_rules.add(v.rule)
                unique.append(v)

        return IntegrityResult(
            passed=len(unique) == 0,
            violations=unique,
        )

    @classmethod
    def check_tool_call(cls, tool_name: str, tool_input: dict) -> IntegrityResult:
        """Check a tool call's name and arguments against integrity rules."""
        # Check tool name
        result = cls.check_text(tool_name)
        if result.blocked:
            return result

        # Check all string values in tool input
        violations = list(result.violations)
        for key, value in tool_input.items():
            if isinstance(value, str):
                sub = cls.check_text(value)
                violations.extend(sub.violations)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        sub = cls.check_text(item)
                        violations.extend(sub.violations)

        # Deduplicate
        seen: set[str] = set()
        unique: list[IntegrityViolation] = []
        for v in violations:
            key = f"{v.rule}:{v.detail}"
            if key not in seen:
                seen.add(key)
                unique.append(v)

        return IntegrityResult(passed=len(unique) == 0, violations=unique)

    @classmethod
    def check_campaign_plan(cls, plan: dict) -> IntegrityResult:
        """Check a campaign plan for integrity violations.

        Walks all string values in the plan dict recursively.
        """
        texts = _extract_strings(plan)
        all_violations: list[IntegrityViolation] = []

        for text in texts:
            result = cls.check_text(text)
            all_violations.extend(result.violations)

        # Deduplicate
        seen: set[str] = set()
        unique: list[IntegrityViolation] = []
        for v in all_violations:
            key = f"{v.rule}:{v.detail}"
            if key not in seen:
                seen.add(key)
                unique.append(v)

        return IntegrityResult(passed=len(unique) == 0, violations=unique)

    @classmethod
    def format_violations(cls, result: IntegrityResult) -> str:
        """Format violations into a human-readable message."""
        if result.passed:
            return "No integrity violations."

        lines = ["Integrity policy violation(s):"]
        for v in result.violations:
            desc = RULE_DESCRIPTIONS.get(v.rule, v.rule)
            lines.append(f"  [{v.severity.upper()}] {v.detail}")
            lines.append(f"    Policy: {desc}")
        return "\n".join(lines)


def _extract_strings(obj: object, max_depth: int = 10) -> list[str]:
    """Recursively extract all string values from a nested structure."""
    if max_depth <= 0:
        return []
    strings: list[str] = []
    if isinstance(obj, str):
        strings.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            strings.extend(_extract_strings(v, max_depth - 1))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            strings.extend(_extract_strings(item, max_depth - 1))
    return strings

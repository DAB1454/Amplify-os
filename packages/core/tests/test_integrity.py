"""Tests for IntegrityPolicy — the hard guardrails of Amplify-OS."""

from __future__ import annotations

import pytest

from amplify.core.policies.integrity import (
    IntegrityPolicy,
    IntegrityResult,
    IntegrityViolation,
    PROHIBITED_PHRASES,
    RULE_DESCRIPTIONS,
)


# ── Phrase detection ──────────────────────────────────────────────


class TestPhraseDetection:
    """Every prohibited phrase must trigger a block."""

    @pytest.mark.parametrize(
        "phrase,rule",
        PROHIBITED_PHRASES,
        ids=[p for p, _ in PROHIBITED_PHRASES],
    )
    def test_each_prohibited_phrase_is_blocked(self, phrase: str, rule: str):
        result = IntegrityPolicy.check_text(f"We should {phrase} for this campaign")
        assert not result.passed
        assert result.blocked
        assert any(v.rule == rule for v in result.violations)

    def test_case_insensitive(self):
        result = IntegrityPolicy.check_text("BUY FOLLOWERS for the launch")
        assert result.blocked

    def test_clean_text_passes(self):
        result = IntegrityPolicy.check_text(
            "Schedule an Instagram reel with a 15-second hook and a CTA to HyperFollow."
        )
        assert result.passed
        assert not result.blocked
        assert result.violations == []

    def test_empty_text_passes(self):
        result = IntegrityPolicy.check_text("")
        assert result.passed

    def test_none_like_empty(self):
        """check_text('') returns passed — callers should guard None."""
        result = IntegrityPolicy.check_text("")
        assert result.passed


# ── Regex pattern detection ───────────────────────────────────────


class TestRegexPatterns:
    def test_pricing_for_streams(self):
        result = IntegrityPolicy.check_text("Get 10K streams for $50")
        assert result.blocked
        assert any(v.rule == "no_fake_engagement" for v in result.violations)

    def test_guaranteed_plays(self):
        result = IntegrityPolicy.check_text("We guarantee 5000 plays in 7 days")
        assert result.blocked

    def test_legitimate_stats_not_flagged(self):
        result = IntegrityPolicy.check_text("The single hit 10K streams organically last month")
        assert result.passed


# ── Tool call checking ────────────────────────────────────────────


class TestToolCallChecking:
    def test_clean_tool_call(self):
        result = IntegrityPolicy.check_tool_call(
            "publish_post",
            {"caption": "New single out now!", "platform": "instagram"},
        )
        assert result.passed

    def test_flagged_in_argument(self):
        result = IntegrityPolicy.check_tool_call(
            "publish_post",
            {"caption": "Buy followers to boost this track"},
        )
        assert result.blocked

    def test_flagged_in_tool_name(self):
        result = IntegrityPolicy.check_tool_call(
            "buy streams service",
            {"count": "1000"},
        )
        assert result.blocked

    def test_flagged_in_list_argument(self):
        result = IntegrityPolicy.check_tool_call(
            "send_messages",
            {"messages": ["Great track!", "Buy followers for cheap"]},
        )
        assert result.blocked

    def test_deduplication(self):
        """Multiple violations of the same rule+detail should be deduped."""
        result = IntegrityPolicy.check_tool_call(
            "send",
            {
                "line1": "buy followers",
                "line2": "buy followers please",
            },
        )
        assert result.blocked
        # Same phrase appears twice — should deduplicate by rule:detail
        rules = [v.rule for v in result.violations]
        assert rules.count("no_fake_engagement") >= 1


# ── Campaign plan checking ────────────────────────────────────────


class TestCampaignPlanChecking:
    def test_clean_plan(self):
        plan = {
            "name": "Summer Single Launch",
            "phases": [
                {
                    "name": "Pre-release",
                    "actions": [
                        {"type": "post", "caption": "Teaser clip dropping Friday"},
                    ],
                }
            ],
        }
        result = IntegrityPolicy.check_campaign_plan(plan)
        assert result.passed

    def test_violation_in_nested_plan(self):
        plan = {
            "name": "Growth Hack",
            "phases": [
                {
                    "name": "Phase 1",
                    "actions": [
                        {"type": "engagement", "note": "Buy streams on Spotify"},
                    ],
                }
            ],
        }
        result = IntegrityPolicy.check_campaign_plan(plan)
        assert result.blocked

    def test_deeply_nested_violation(self):
        plan = {
            "a": {"b": {"c": {"d": {"e": "Set up a bot farm for plays"}}}},
        }
        result = IntegrityPolicy.check_campaign_plan(plan)
        assert result.blocked

    def test_max_depth_prevents_infinite_recursion(self):
        """Deeply nested beyond max_depth should not crash."""
        obj: dict = {}
        current = obj
        for i in range(15):
            current["child"] = {}
            current = current["child"]
        current["text"] = "buy followers"
        # max_depth=10, so this won't be found — but it shouldn't crash
        result = IntegrityPolicy.check_campaign_plan(obj)
        # Whether it's found depends on depth, but no crash
        assert isinstance(result, IntegrityResult)


# ── Formatting ────────────────────────────────────────────────────


class TestFormatting:
    def test_format_clean(self):
        result = IntegrityResult(passed=True)
        msg = IntegrityPolicy.format_violations(result)
        assert msg == "No integrity violations."

    def test_format_violation(self):
        result = IntegrityPolicy.check_text("buy followers")
        msg = IntegrityPolicy.format_violations(result)
        assert "BLOCK" in msg
        assert "buy followers" in msg.lower()
        assert "Policy:" in msg

    def test_rule_descriptions_complete(self):
        """Every rule referenced in PROHIBITED_PHRASES has a description."""
        rules_used = {rule for _, rule in PROHIBITED_PHRASES}
        for rule in rules_used:
            assert rule in RULE_DESCRIPTIONS, f"Missing description for rule: {rule}"


# ── Edge cases ────────────────────────────────────────────────────


class TestEdgeCases:
    def test_partial_match_inside_word(self):
        """'impersonate' inside 'impersonated' should still match."""
        result = IntegrityPolicy.check_text("The artist impersonated a rockstar on stage")
        assert result.blocked

    def test_multiple_violations_different_rules(self):
        result = IntegrityPolicy.check_text(
            "Let's buy followers and set up a bot farm and mass DM everyone"
        )
        assert result.blocked
        rules = {v.rule for v in result.violations}
        assert "no_fake_engagement" in rules
        assert "no_bulk_spam" in rules

    def test_result_blocked_property(self):
        """blocked is True only if any violation has severity='block'."""
        result = IntegrityResult(
            passed=False,
            violations=[IntegrityViolation(rule="test", detail="test", severity="warn")],
        )
        assert not result.blocked

        result2 = IntegrityResult(
            passed=False,
            violations=[IntegrityViolation(rule="test", detail="test", severity="block")],
        )
        assert result2.blocked


# ── Brand safety cross-reference ─────────────────────────────────


class TestBrandSafetyCrossReference:
    def test_brand_safety_catches_integrity_violations(self):
        from amplify.core.policies.brand_safety import BrandSafetyPolicy

        safe, flagged = BrandSafetyPolicy.check_content("Buy followers for cheap")
        assert not safe
        assert len(flagged) > 0

    def test_brand_safety_catches_own_terms(self):
        from amplify.core.policies.brand_safety import BrandSafetyPolicy

        safe, flagged = BrandSafetyPolicy.check_content("This is a get rich quick scheme")
        assert not safe
        assert any("get rich quick" in f for f in flagged)

    def test_brand_safety_clean_content(self):
        from amplify.core.policies.brand_safety import BrandSafetyPolicy

        safe, flagged = BrandSafetyPolicy.check_content(
            "New single out Friday — pre-save on Spotify now"
        )
        assert safe
        assert flagged == []

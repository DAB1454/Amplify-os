"""Tests for the policy engine and all 7 rules."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from amplify.core.policies.engine import (
    ActionContext,
    Decision,
    PolicyEngine,
    PolicyResult,
    PolicyVerdict,
    create_default_engine,
)
from amplify.core.policies.rules.burst_limit import BurstLimitRule
from amplify.core.policies.rules.duplicate_caption import DuplicateCaptionRule
from amplify.core.policies.rules.frequency_limit import FrequencyLimitRule
from amplify.core.policies.rules.hours_restriction import HoursRestrictionRule
from amplify.core.policies.rules.missing_link import MissingLinkRule
from amplify.core.policies.rules.release_mapping import ReleaseMappingRule
from amplify.core.policies.rules.reply_safety import ReplySafetyRule


# ── Engine core ──────────────────────────────────────────────────


class TestPolicyEngine:
    def test_empty_engine_allows(self):
        engine = PolicyEngine()
        ctx = ActionContext(action_type="publish")
        result = engine.evaluate(ctx)
        assert result.allowed

    def test_block_wins_over_allow(self):
        engine = PolicyEngine()

        class AllowRule:
            name = "allow_all"
            def evaluate(self, ctx): return PolicyVerdict(rule_name=self.name, decision=Decision.ALLOW)

        class BlockRule:
            name = "block_all"
            def evaluate(self, ctx): return PolicyVerdict(rule_name=self.name, decision=Decision.BLOCK, reason="nope")

        engine.add_rule(AllowRule())
        engine.add_rule(BlockRule())
        result = engine.evaluate(ActionContext())
        assert result.blocked
        assert "nope" in result.blocking_reasons

    def test_approval_wins_over_allow(self):
        engine = PolicyEngine()

        class ApprovalRule:
            name = "need_approval"
            def evaluate(self, ctx): return PolicyVerdict(rule_name=self.name, decision=Decision.REQUIRE_APPROVAL, reason="check it")

        engine.add_rule(ApprovalRule())
        result = engine.evaluate(ActionContext())
        assert result.needs_approval
        assert "check it" in result.approval_reasons

    def test_block_wins_over_approval(self):
        engine = PolicyEngine()

        class ApprovalRule:
            name = "approval"
            def evaluate(self, ctx): return PolicyVerdict(rule_name=self.name, decision=Decision.REQUIRE_APPROVAL, reason="soft")

        class BlockRule:
            name = "block"
            def evaluate(self, ctx): return PolicyVerdict(rule_name=self.name, decision=Decision.BLOCK, reason="hard")

        engine.add_rule(ApprovalRule())
        engine.add_rule(BlockRule())
        result = engine.evaluate(ActionContext())
        assert result.blocked

    def test_exception_in_rule_blocks(self):
        engine = PolicyEngine()

        class BrokenRule:
            name = "broken"
            def evaluate(self, ctx): raise RuntimeError("oops")

        engine.add_rule(BrokenRule())
        result = engine.evaluate(ActionContext())
        assert result.blocked
        assert "internal error" in result.blocking_reasons[0]

    def test_summary_format(self):
        result = PolicyResult(
            final_decision=Decision.BLOCK,
            verdicts=[
                PolicyVerdict(rule_name="r1", decision=Decision.BLOCK, reason="blocked reason"),
                PolicyVerdict(rule_name="r2", decision=Decision.ALLOW),
            ],
        )
        s = result.summary()
        assert "block" in s.lower()
        assert "blocked reason" in s


class TestDefaultEngine:
    def test_creates_with_all_rules(self):
        engine = create_default_engine()
        names = {r.name for r in engine.rules}
        assert names == {
            "duplicate_caption",
            "frequency_limit",
            "missing_link",
            "release_mapping",
            "reply_safety",
            "hours_restriction",
            "burst_limit",
        }

    def test_clean_action_allowed(self):
        engine = create_default_engine()
        ctx = ActionContext(
            action_type="publish",
            platform="instagram",
            content="New track dropping! Link in bio",
            artist_id="artist_001",
            release_id="release_001",
            destination_url="https://linktr.ee/artist",
            now=datetime(2026, 3, 29, 14, 0, 0),
        )
        result = engine.evaluate(ctx)
        assert result.allowed


# ── Duplicate Caption Rule ───────────────────────────────────────


class TestDuplicateCaptionRule:
    def test_no_history_allows(self):
        rule = DuplicateCaptionRule()
        ctx = ActionContext(content="Hello world")
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_exact_duplicate_blocks(self):
        rule = DuplicateCaptionRule()
        ctx = ActionContext(
            content="Stream my new single now!",
            recent_captions=["Stream my new single now!"],
        )
        v = rule.evaluate(ctx)
        assert v.decision == Decision.BLOCK
        assert "duplicate" in v.reason.lower()

    def test_near_duplicate_needs_approval(self):
        rule = DuplicateCaptionRule(threshold=0.8)
        ctx = ActionContext(
            content="Stream my new single right now!",
            recent_captions=["Stream my new single now!"],
        )
        v = rule.evaluate(ctx)
        assert v.decision == Decision.REQUIRE_APPROVAL

    def test_different_content_allows(self):
        rule = DuplicateCaptionRule()
        ctx = ActionContext(
            content="Behind the scenes of the album recording session",
            recent_captions=["New single out now!"],
        )
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_reply_skipped(self):
        rule = DuplicateCaptionRule()
        ctx = ActionContext(
            action_type="reply",
            content="Thank you!",
            recent_captions=["Thank you!"],
        )
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_case_insensitive(self):
        rule = DuplicateCaptionRule()
        ctx = ActionContext(
            content="STREAM MY NEW SINGLE NOW!",
            recent_captions=["stream my new single now!"],
        )
        v = rule.evaluate(ctx)
        assert v.decision == Decision.BLOCK

    def test_empty_content_allows(self):
        rule = DuplicateCaptionRule()
        ctx = ActionContext(content="", recent_captions=["something"])
        assert rule.evaluate(ctx).decision == Decision.ALLOW


# ── Frequency Limit Rule ────────────────────────────────────────


class TestFrequencyLimitRule:
    def test_no_history_allows(self):
        rule = FrequencyLimitRule()
        ctx = ActionContext(platform="instagram")
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_too_soon_blocks(self):
        rule = FrequencyLimitRule(min_interval_minutes=15)
        now = datetime(2026, 3, 29, 10, 0, 0)
        ctx = ActionContext(
            platform="instagram",
            now=now,
            recent_posts=[{"published_at": now - timedelta(minutes=5)}],
        )
        v = rule.evaluate(ctx)
        assert v.decision == Decision.BLOCK
        assert "too soon" in v.reason.lower()

    def test_hourly_limit_blocks(self):
        rule = FrequencyLimitRule(max_per_hour=3, min_interval_minutes=1)
        now = datetime(2026, 3, 29, 10, 0, 0)
        posts = [{"published_at": now - timedelta(minutes=i * 5)} for i in range(1, 4)]
        ctx = ActionContext(platform="tiktok", now=now, recent_posts=posts)
        v = rule.evaluate(ctx)
        assert v.decision == Decision.BLOCK
        assert "hourly" in v.reason.lower()

    def test_daily_limit_blocks(self):
        rule = FrequencyLimitRule(max_per_day=3, min_interval_minutes=1)
        now = datetime(2026, 3, 29, 22, 0, 0)
        posts = [{"published_at": now - timedelta(hours=i * 3)} for i in range(1, 4)]
        ctx = ActionContext(platform="youtube", now=now, recent_posts=posts)
        v = rule.evaluate(ctx)
        assert v.decision == Decision.BLOCK
        assert "daily" in v.reason.lower()

    def test_within_limits_allows(self):
        rule = FrequencyLimitRule(max_per_hour=3, max_per_day=12, min_interval_minutes=15)
        now = datetime(2026, 3, 29, 10, 0, 0)
        ctx = ActionContext(
            platform="instagram",
            now=now,
            recent_posts=[{"published_at": now - timedelta(minutes=30)}],
        )
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_reply_skipped(self):
        rule = FrequencyLimitRule()
        now = datetime(2026, 3, 29, 10, 0, 0)
        ctx = ActionContext(
            action_type="reply",
            now=now,
            recent_posts=[{"published_at": now - timedelta(seconds=10)}],
        )
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_string_timestamps_parsed(self):
        rule = FrequencyLimitRule(min_interval_minutes=15)
        now = datetime(2026, 3, 29, 10, 0, 0)
        ctx = ActionContext(
            platform="instagram",
            now=now,
            recent_posts=[{"published_at": (now - timedelta(minutes=5)).isoformat()}],
        )
        v = rule.evaluate(ctx)
        assert v.decision == Decision.BLOCK


# ── Missing Link Rule ───────────────────────────────────────────


class TestMissingLinkRule:
    def test_with_destination_url_allows(self):
        rule = MissingLinkRule()
        ctx = ActionContext(destination_url="https://linktr.ee/artist")
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_url_in_content_allows(self):
        rule = MissingLinkRule()
        ctx = ActionContext(content="Check it out https://spotify.com/track/123")
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_link_in_bio_allows(self):
        rule = MissingLinkRule()
        ctx = ActionContext(content="New music out now! Link in bio")
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_no_link_needs_approval(self):
        rule = MissingLinkRule()
        ctx = ActionContext(content="New music out now!")
        v = rule.evaluate(ctx)
        assert v.decision == Decision.REQUIRE_APPROVAL
        assert "destination link" in v.reason.lower()

    def test_reply_skipped(self):
        rule = MissingLinkRule()
        ctx = ActionContext(action_type="reply", content="Thanks!")
        assert rule.evaluate(ctx).decision == Decision.ALLOW


# ── Release Mapping Rule ────────────────────────────────────────


class TestReleaseMappingRule:
    def test_with_artist_and_release_allows(self):
        rule = ReleaseMappingRule()
        ctx = ActionContext(artist_id="a1", release_id="r1")
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_with_artist_and_campaign_allows(self):
        rule = ReleaseMappingRule()
        ctx = ActionContext(artist_id="a1", campaign_id="c1")
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_no_artist_blocks(self):
        rule = ReleaseMappingRule()
        ctx = ActionContext(release_id="r1")
        v = rule.evaluate(ctx)
        assert v.decision == Decision.BLOCK
        assert "no artist" in v.reason.lower()

    def test_artist_without_release_needs_approval(self):
        rule = ReleaseMappingRule()
        ctx = ActionContext(artist_id="a1")
        v = rule.evaluate(ctx)
        assert v.decision == Decision.REQUIRE_APPROVAL

    def test_reply_skipped(self):
        rule = ReleaseMappingRule()
        ctx = ActionContext(action_type="reply")
        assert rule.evaluate(ctx).decision == Decision.ALLOW


# ── Reply Safety Rule ───────────────────────────────────────────


class TestReplySafetyRule:
    def test_non_reply_skipped(self):
        rule = ReplySafetyRule()
        ctx = ActionContext(action_type="publish", content="click here")
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_clean_reply_allows(self):
        rule = ReplySafetyRule()
        ctx = ActionContext(action_type="reply", content="Thank you so much for listening!")
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_spam_phrase_blocks(self):
        rule = ReplySafetyRule()
        ctx = ActionContext(action_type="reply", content="Click here for exclusive content!")
        v = rule.evaluate(ctx)
        assert v.decision == Decision.BLOCK
        assert "click here" in v.reason.lower()

    def test_follow_for_follow_blocks(self):
        rule = ReplySafetyRule()
        ctx = ActionContext(action_type="reply", content="Follow for follow! F4F!")
        v = rule.evaluate(ctx)
        assert v.decision == Decision.BLOCK

    def test_guaranteed_blocks(self):
        rule = ReplySafetyRule()
        ctx = ActionContext(action_type="reply", content="This is guaranteed to change your life!")
        v = rule.evaluate(ctx)
        assert v.decision == Decision.BLOCK

    def test_url_in_reply_needs_approval(self):
        rule = ReplySafetyRule()
        ctx = ActionContext(action_type="reply", content="Check out https://example.com for more info")
        v = rule.evaluate(ctx)
        assert v.decision == Decision.REQUIRE_APPROVAL
        assert "URL" in v.reason

    def test_dollar_amount_needs_approval(self):
        rule = ReplySafetyRule()
        ctx = ActionContext(action_type="reply", content="You can get this for only $29!")
        v = rule.evaluate(ctx)
        assert v.decision == Decision.REQUIRE_APPROVAL

    def test_violent_language_needs_approval(self):
        rule = ReplySafetyRule()
        ctx = ActionContext(action_type="reply", content="This beat is a threat to the competition!")
        v = rule.evaluate(ctx)
        assert v.decision == Decision.REQUIRE_APPROVAL

    def test_empty_reply_allows(self):
        rule = ReplySafetyRule()
        ctx = ActionContext(action_type="reply", content="")
        assert rule.evaluate(ctx).decision == Decision.ALLOW


# ── Hours Restriction Rule ──────────────────────────────────────


class TestHoursRestrictionRule:
    def test_within_hours_allows(self):
        rule = HoursRestrictionRule(allowed_start=6, allowed_end=23)
        ctx = ActionContext(now=datetime(2026, 3, 29, 14, 0, 0))
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_before_start_needs_approval(self):
        rule = HoursRestrictionRule(allowed_start=6, allowed_end=23)
        ctx = ActionContext(now=datetime(2026, 3, 29, 3, 0, 0))
        v = rule.evaluate(ctx)
        assert v.decision == Decision.REQUIRE_APPROVAL
        assert "outside allowed hours" in v.reason.lower()

    def test_after_end_needs_approval(self):
        rule = HoursRestrictionRule(allowed_start=6, allowed_end=23)
        ctx = ActionContext(now=datetime(2026, 3, 29, 23, 30, 0))
        v = rule.evaluate(ctx)
        assert v.decision == Decision.REQUIRE_APPROVAL

    def test_at_start_boundary_allows(self):
        rule = HoursRestrictionRule(allowed_start=6, allowed_end=23)
        ctx = ActionContext(now=datetime(2026, 3, 29, 6, 0, 0))
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_at_end_boundary_needs_approval(self):
        rule = HoursRestrictionRule(allowed_start=6, allowed_end=23)
        ctx = ActionContext(now=datetime(2026, 3, 29, 23, 0, 0))
        v = rule.evaluate(ctx)
        assert v.decision == Decision.REQUIRE_APPROVAL

    def test_custom_hours(self):
        rule = HoursRestrictionRule(allowed_start=9, allowed_end=17)
        assert rule.evaluate(ActionContext(now=datetime(2026, 3, 29, 8, 0, 0))).decision == Decision.REQUIRE_APPROVAL
        assert rule.evaluate(ActionContext(now=datetime(2026, 3, 29, 12, 0, 0))).decision == Decision.ALLOW
        assert rule.evaluate(ActionContext(now=datetime(2026, 3, 29, 17, 0, 0))).decision == Decision.REQUIRE_APPROVAL

    def test_scheduled_at_used_over_now(self):
        rule = HoursRestrictionRule(allowed_start=6, allowed_end=23)
        ctx = ActionContext(
            now=datetime(2026, 3, 29, 10, 0, 0),  # good time
            scheduled_at=datetime(2026, 3, 29, 2, 0, 0),  # bad time
        )
        v = rule.evaluate(ctx)
        assert v.decision == Decision.REQUIRE_APPROVAL

    def test_midnight_needs_approval(self):
        rule = HoursRestrictionRule(allowed_start=6, allowed_end=23)
        ctx = ActionContext(now=datetime(2026, 3, 29, 0, 0, 0))
        assert rule.evaluate(ctx).decision == Decision.REQUIRE_APPROVAL

    def test_one_minute_before_start_needs_approval(self):
        rule = HoursRestrictionRule(allowed_start=6, allowed_end=23)
        ctx = ActionContext(now=datetime(2026, 3, 29, 5, 59, 0))
        assert rule.evaluate(ctx).decision == Decision.REQUIRE_APPROVAL

    def test_one_minute_after_start_allows(self):
        rule = HoursRestrictionRule(allowed_start=6, allowed_end=23)
        ctx = ActionContext(now=datetime(2026, 3, 29, 6, 1, 0))
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_one_minute_before_end_allows(self):
        rule = HoursRestrictionRule(allowed_start=6, allowed_end=23)
        ctx = ActionContext(now=datetime(2026, 3, 29, 22, 59, 0))
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_one_minute_after_end_needs_approval(self):
        rule = HoursRestrictionRule(allowed_start=6, allowed_end=23)
        ctx = ActionContext(now=datetime(2026, 3, 29, 23, 1, 0))
        assert rule.evaluate(ctx).decision == Decision.REQUIRE_APPROVAL

    def test_last_second_of_day_needs_approval(self):
        rule = HoursRestrictionRule(allowed_start=6, allowed_end=23)
        ctx = ActionContext(now=datetime(2026, 3, 29, 23, 59, 59))
        assert rule.evaluate(ctx).decision == Decision.REQUIRE_APPROVAL


# ── Burst Limit Rule ────────────────────────────────────────────


class TestBurstLimitRule:
    def test_no_posts_allows(self):
        rule = BurstLimitRule()
        assert rule.evaluate(ActionContext()).decision == Decision.ALLOW

    def test_under_limit_allows(self):
        rule = BurstLimitRule(window_minutes=30, max_posts=5)
        now = datetime(2026, 3, 29, 10, 0, 0)
        posts = [{"published_at": now - timedelta(minutes=i * 5)} for i in range(1, 4)]
        ctx = ActionContext(now=now, recent_posts=posts)
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_at_limit_blocks(self):
        rule = BurstLimitRule(window_minutes=30, max_posts=5)
        now = datetime(2026, 3, 29, 10, 0, 0)
        posts = [{"published_at": now - timedelta(minutes=i * 3)} for i in range(1, 6)]
        ctx = ActionContext(now=now, recent_posts=posts)
        v = rule.evaluate(ctx)
        assert v.decision == Decision.BLOCK
        assert "burst limit" in v.reason.lower()

    def test_approaching_limit_needs_approval(self):
        rule = BurstLimitRule(window_minutes=30, max_posts=5)
        now = datetime(2026, 3, 29, 10, 0, 0)
        posts = [{"published_at": now - timedelta(minutes=i * 5)} for i in range(1, 5)]
        ctx = ActionContext(now=now, recent_posts=posts)
        v = rule.evaluate(ctx)
        assert v.decision == Decision.REQUIRE_APPROVAL
        assert "approaching" in v.reason.lower()

    def test_old_posts_outside_window(self):
        rule = BurstLimitRule(window_minutes=30, max_posts=3)
        now = datetime(2026, 3, 29, 10, 0, 0)
        posts = [{"published_at": now - timedelta(hours=2)}] * 10
        ctx = ActionContext(now=now, recent_posts=posts)
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_reply_skipped(self):
        rule = BurstLimitRule(window_minutes=5, max_posts=1)
        now = datetime(2026, 3, 29, 10, 0, 0)
        ctx = ActionContext(
            action_type="reply",
            now=now,
            recent_posts=[{"published_at": now - timedelta(minutes=1)}],
        )
        assert rule.evaluate(ctx).decision == Decision.ALLOW

    def test_custom_window(self):
        rule = BurstLimitRule(window_minutes=10, max_posts=2)
        now = datetime(2026, 3, 29, 10, 0, 0)
        posts = [
            {"published_at": now - timedelta(minutes=3)},
            {"published_at": now - timedelta(minutes=7)},
        ]
        ctx = ActionContext(now=now, recent_posts=posts)
        v = rule.evaluate(ctx)
        assert v.decision == Decision.BLOCK

    def test_string_timestamps(self):
        rule = BurstLimitRule(window_minutes=30, max_posts=2)
        now = datetime(2026, 3, 29, 10, 0, 0)
        posts = [
            {"published_at": (now - timedelta(minutes=5)).isoformat()},
            {"published_at": (now - timedelta(minutes=10)).isoformat()},
        ]
        ctx = ActionContext(now=now, recent_posts=posts)
        v = rule.evaluate(ctx)
        assert v.decision == Decision.BLOCK


# ── Integration: full engine scenarios ───────────────────────────


class TestFullEngineScenarios:
    def test_clean_publish_allowed(self):
        engine = create_default_engine()
        ctx = ActionContext(
            action_type="publish",
            platform="instagram",
            content="New single dropping Friday! Link in bio",
            artist_id="a1",
            release_id="r1",
            destination_url="https://linktr.ee/drew",
            now=datetime(2026, 3, 29, 14, 0, 0),
        )
        result = engine.evaluate(ctx)
        assert result.allowed

    def test_duplicate_plus_no_link_blocks(self):
        engine = create_default_engine()
        ctx = ActionContext(
            action_type="publish",
            platform="instagram",
            content="Stream my single!",
            recent_captions=["Stream my single!"],
            artist_id="a1",
            release_id="r1",
            now=datetime(2026, 3, 29, 14, 0, 0),
        )
        result = engine.evaluate(ctx)
        assert result.blocked  # duplicate is exact -> BLOCK

    def test_off_hours_with_no_release_stacks(self):
        engine = create_default_engine()
        ctx = ActionContext(
            action_type="publish",
            platform="tiktok",
            content="Quick update https://example.com",
            artist_id="a1",
            now=datetime(2026, 3, 29, 2, 0, 0),  # 2 AM
        )
        result = engine.evaluate(ctx)
        assert result.needs_approval
        # Both hours_restriction and release_mapping should flag
        assert len(result.approval_reasons) >= 2

    def test_spam_reply_blocked(self):
        engine = create_default_engine()
        ctx = ActionContext(
            action_type="reply",
            platform="instagram",
            content="Follow for follow! Check my page!",
            artist_id="a1",
        )
        result = engine.evaluate(ctx)
        assert result.blocked

    def test_clean_reply_allowed(self):
        engine = create_default_engine()
        ctx = ActionContext(
            action_type="reply",
            platform="instagram",
            content="Thank you so much for the love! Means the world.",
            artist_id="a1",
        )
        result = engine.evaluate(ctx)
        assert result.allowed

    def test_burst_blocks_rapid_posting(self):
        engine = create_default_engine(burst_window_minutes=30, burst_max_posts=3)
        now = datetime(2026, 3, 29, 14, 0, 0)
        ctx = ActionContext(
            action_type="publish",
            platform="instagram",
            content="Another post! Link in bio",
            artist_id="a1",
            release_id="r1",
            destination_url="https://linktr.ee/drew",
            now=now,
            recent_posts=[
                {"published_at": now - timedelta(minutes=5)},
                {"published_at": now - timedelta(minutes=10)},
                {"published_at": now - timedelta(minutes=20)},
            ],
        )
        result = engine.evaluate(ctx)
        assert result.blocked

    def test_no_artist_blocks_even_with_everything_else(self):
        engine = create_default_engine()
        ctx = ActionContext(
            action_type="publish",
            platform="instagram",
            content="New music! Link in bio",
            destination_url="https://linktr.ee/artist",
            now=datetime(2026, 3, 29, 14, 0, 0),
        )
        result = engine.evaluate(ctx)
        assert result.blocked
        assert any("artist" in r.lower() for r in result.blocking_reasons)

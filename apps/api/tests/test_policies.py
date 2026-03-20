"""Tests for core policy enforcement."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from amplify.core.policies.anti_spam import AntiSpamPolicy
from amplify.core.policies.brand_safety import BrandSafetyPolicy
from amplify.core.policies.rate_limits import PlatformRateLimits, get_rate_limit


# ---------------------------------------------------------------------------
# AntiSpamPolicy
# ---------------------------------------------------------------------------


class TestAntiSpamPolicy:
    # Fixed reference time — no test depends on wall clock
    NOW = datetime(2026, 3, 29, 14, 0, 0)

    def test_anti_spam_allows_first_post(self):
        """First post with no history should always be allowed."""
        allowed, reason = AntiSpamPolicy.check_rate_limit(
            tenant_id=uuid4(),
            platform="instagram",
            recent_posts=[],
            now=self.NOW,
        )
        assert allowed is True
        assert reason == "OK"

    def test_anti_spam_blocks_over_hourly_limit(self):
        """Should block when hourly post limit is exceeded."""
        # Create MAX_POSTS_PER_HOUR posts in the last hour, oldest first,
        # with the most recent at 16 min ago (past the minimum interval).
        recent = [
            {"published_at": self.NOW - timedelta(minutes=16 + i * 16)}
            for i in range(AntiSpamPolicy.MAX_POSTS_PER_HOUR)
        ]
        allowed, reason = AntiSpamPolicy.check_rate_limit(
            tenant_id=uuid4(),
            platform="instagram",
            recent_posts=recent,
            now=self.NOW,
        )
        assert allowed is False
        assert "limit" in reason.lower() or "too soon" in reason.lower()

    def test_anti_spam_blocks_over_daily_limit(self):
        """Should block when daily post limit is exceeded."""
        # Create MAX_POSTS_PER_DAY posts spread across the last 24 hours,
        # with the most recent at 1.5h ago (past the minimum interval).
        recent = [
            {"published_at": self.NOW - timedelta(hours=1.5 + i * 1.5)}
            for i in range(AntiSpamPolicy.MAX_POSTS_PER_DAY)
        ]
        allowed, reason = AntiSpamPolicy.check_rate_limit(
            tenant_id=uuid4(),
            platform="tiktok",
            recent_posts=recent,
            now=self.NOW,
        )
        assert allowed is False
        assert "limit" in reason.lower() or "too soon" in reason.lower()

    def test_anti_spam_blocks_too_frequent_posts(self):
        """Should block posts that come too quickly after the last one."""
        recent = [
            {"published_at": self.NOW - timedelta(minutes=2)},
        ]
        allowed, reason = AntiSpamPolicy.check_rate_limit(
            tenant_id=uuid4(),
            platform="instagram",
            recent_posts=recent,
            now=self.NOW,
        )
        assert allowed is False
        assert "Too soon" in reason

    def test_anti_spam_allows_after_interval(self):
        """Should allow a post if enough time has passed since the last one."""
        recent = [
            {"published_at": self.NOW - timedelta(minutes=20)},
        ]
        allowed, reason = AntiSpamPolicy.check_rate_limit(
            tenant_id=uuid4(),
            platform="instagram",
            recent_posts=recent,
            now=self.NOW,
        )
        assert allowed is True
        assert reason == "OK"

    def test_anti_spam_uses_scheduled_at_fallback(self):
        """Should read scheduled_at when published_at is absent."""
        recent = [
            {"scheduled_at": self.NOW - timedelta(minutes=1)},
        ]
        allowed, reason = AntiSpamPolicy.check_rate_limit(
            tenant_id=uuid4(),
            platform="tiktok",
            recent_posts=recent,
            now=self.NOW,
        )
        assert allowed is False
        assert "Too soon" in reason


# ---------------------------------------------------------------------------
# BrandSafetyPolicy
# ---------------------------------------------------------------------------


class TestBrandSafetyPolicy:
    def test_brand_safety_passes_clean_content(self):
        safe, flagged = BrandSafetyPolicy.check_content(
            "Check out our new single dropping Friday! Pre-save now."
        )
        assert safe is True
        assert flagged == []

    def test_brand_safety_flags_blocked_words(self):
        safe, flagged = BrandSafetyPolicy.check_content(
            "Buy followers today and get guaranteed results!"
        )
        assert safe is False
        flagged_lower = " ".join(flagged).lower()
        assert "buy followers" in flagged_lower
        assert "guaranteed results" in flagged_lower

    def test_brand_safety_case_insensitive(self):
        safe, flagged = BrandSafetyPolicy.check_content(
            "GET RICH QUICK with this new track"
        )
        assert safe is False
        assert "get rich quick" in flagged

    def test_brand_safety_flags_free_money(self):
        safe, flagged = BrandSafetyPolicy.check_content(
            "Win free money by streaming our song"
        )
        assert safe is False
        assert "free money" in flagged

    def test_brand_safety_image_check_stub(self):
        safe, reason = BrandSafetyPolicy.check_image_url(
            "https://example.com/image.jpg"
        )
        assert safe is True


# ---------------------------------------------------------------------------
# Rate Limits
# ---------------------------------------------------------------------------


class TestRateLimits:
    def test_rate_limits_returns_instagram_limits(self):
        limits = get_rate_limit("instagram")
        assert isinstance(limits, PlatformRateLimits)
        assert limits.platform == "instagram"
        assert limits.posts_per_day == 25
        assert limits.comments_per_hour == 200

    def test_rate_limits_returns_tiktok_limits(self):
        limits = get_rate_limit("tiktok")
        assert limits.platform == "tiktok"
        assert limits.posts_per_day == 10

    def test_rate_limits_returns_youtube_limits(self):
        limits = get_rate_limit("youtube")
        assert limits.platform == "youtube"
        assert limits.posts_per_day == 6
        assert limits.uploads_per_day == 6
        assert limits.comments_per_minute == 50

    def test_rate_limits_returns_default_for_unknown(self):
        limits = get_rate_limit("mastodon")
        assert limits.platform == "mastodon"
        assert limits.posts_per_day == 50

    def test_rate_limits_case_insensitive(self):
        limits = get_rate_limit("INSTAGRAM")
        assert limits.platform == "instagram"
        assert limits.posts_per_day == 25

"""Tests for publish_post and moderate_comments worker jobs with policy enforcement."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.jobs.publish_post import publish_post
from app.jobs.moderate_comments import moderate_comments
from app.jobs.send_alerts import send_alerts

# Fixed midday time — all tests use this instead of wall clock
_MIDDAY = datetime(2026, 3, 29, 14, 0, 0)


class TestPublishPostJob:
    @pytest.mark.asyncio
    async def test_clean_publish_allowed(self):
        result = await publish_post({
            "platform": "instagram",
            "content": "New music out now! Link in bio",
            "artist_id": "artist_001",
            "release_id": "release_001",
            "destination_url": "https://linktr.ee/drew",
            "now": _MIDDAY.isoformat(),
        })
        assert result["status"] == "ok"
        assert result["policy_decision"] == "allow"
        assert result["published"] is True

    @pytest.mark.asyncio
    async def test_published_result_has_permalink(self):
        result = await publish_post({
            "post_id": "post_123",
            "platform": "instagram",
            "content": "New music out now! Link in bio",
            "artist_id": "artist_001",
            "release_id": "release_001",
            "destination_url": "https://linktr.ee/drew",
            "now": _MIDDAY.isoformat(),
        })
        assert result["permalink"] is not None
        assert result["platform_post_id"] is not None
        assert result["published_at"] is not None

    @pytest.mark.asyncio
    async def test_no_artist_blocked(self):
        result = await publish_post({
            "platform": "instagram",
            "content": "Random post with link in bio",
            "destination_url": "https://example.com",
            "now": _MIDDAY.isoformat(),
        })
        assert result["status"] == "blocked"
        assert any("artist" in r.lower() for r in result["reasons"])

    @pytest.mark.asyncio
    async def test_duplicate_caption_blocked(self):
        result = await publish_post({
            "platform": "tiktok",
            "content": "Stream my new single now!",
            "artist_id": "artist_001",
            "release_id": "release_001",
            "destination_url": "https://linktr.ee/drew",
            "recent_captions": ["Stream my new single now!"],
            "now": _MIDDAY.isoformat(),
        })
        assert result["status"] == "blocked"

    @pytest.mark.asyncio
    async def test_too_frequent_blocked(self):
        result = await publish_post({
            "platform": "instagram",
            "content": "Post with link in bio",
            "artist_id": "a1",
            "release_id": "r1",
            "destination_url": "https://linktr.ee/drew",
            "now": _MIDDAY.isoformat(),
            "recent_posts": [
                {"published_at": (_MIDDAY - timedelta(minutes=2)).isoformat()},
            ],
        })
        assert result["status"] == "blocked"
        assert any("too soon" in r.lower() for r in result["reasons"])

    @pytest.mark.asyncio
    async def test_missing_link_needs_approval(self):
        result = await publish_post({
            "platform": "instagram",
            "content": "New music vibes",
            "artist_id": "a1",
            "release_id": "r1",
            "now": _MIDDAY.isoformat(),
        })
        assert result["status"] == "pending_approval"
        assert any("destination" in r.lower() for r in result["reasons"])

    @pytest.mark.asyncio
    async def test_no_release_needs_approval(self):
        result = await publish_post({
            "platform": "youtube",
            "content": "Behind the scenes https://youtube.com/watch?v=x",
            "artist_id": "a1",
            "destination_url": "https://youtube.com",
            "now": _MIDDAY.isoformat(),
        })
        assert result["status"] == "pending_approval"

    @pytest.mark.asyncio
    async def test_dry_run_does_not_publish(self):
        result = await publish_post({
            "platform": "instagram",
            "content": "New music out now! Link in bio",
            "artist_id": "artist_001",
            "release_id": "release_001",
            "destination_url": "https://linktr.ee/drew",
            "dry_run": True,
            "now": _MIDDAY.isoformat(),
        })
        assert result["status"] == "dry_run"
        assert result["published"] is False
        assert "preview" in result
        assert result["preview"]["platform"] == "instagram"

    @pytest.mark.asyncio
    async def test_post_id_in_result(self):
        result = await publish_post({
            "post_id": "abc123",
            "platform": "instagram",
            "content": "Post with link in bio",
            "artist_id": "a1",
            "release_id": "r1",
            "destination_url": "https://linktr.ee/drew",
            "now": _MIDDAY.isoformat(),
        })
        assert result["post_id"] == "abc123"


class TestModerateCommentsJob:
    @pytest.mark.asyncio
    async def test_empty_replies(self):
        result = await moderate_comments({
            "platform": "instagram",
            "artist_id": "a1",
            "replies": [],
        })
        assert result["comments_processed"] == 0

    @pytest.mark.asyncio
    async def test_clean_replies_approved(self):
        result = await moderate_comments({
            "platform": "instagram",
            "artist_id": "a1",
            "replies": [
                {"comment_id": "c1", "reply_text": "Thank you so much!"},
                {"comment_id": "c2", "reply_text": "Glad you enjoyed the track!"},
            ],
        })
        assert result["comments_processed"] == 2
        assert len(result["approved"]) == 2
        assert len(result["blocked"]) == 0

    @pytest.mark.asyncio
    async def test_spam_reply_blocked(self):
        result = await moderate_comments({
            "platform": "instagram",
            "artist_id": "a1",
            "replies": [
                {"comment_id": "c1", "reply_text": "Follow for follow! F4F!"},
            ],
        })
        assert len(result["blocked"]) == 1
        assert result["blocked"][0]["comment_id"] == "c1"

    @pytest.mark.asyncio
    async def test_risky_reply_needs_approval(self):
        result = await moderate_comments({
            "platform": "tiktok",
            "artist_id": "a1",
            "replies": [
                {"comment_id": "c1", "reply_text": "Check it out at https://example.com"},
            ],
        })
        assert len(result["needs_approval"]) == 1

    @pytest.mark.asyncio
    async def test_mixed_replies(self):
        result = await moderate_comments({
            "platform": "youtube",
            "artist_id": "a1",
            "replies": [
                {"comment_id": "c1", "reply_text": "Thanks for listening!"},
                {"comment_id": "c2", "reply_text": "Click here for free stuff!"},
                {"comment_id": "c3", "reply_text": "Check https://example.com for more"},
            ],
        })
        assert len(result["approved"]) == 1
        assert len(result["blocked"]) == 1
        assert len(result["needs_approval"]) == 1
        assert result["comments_processed"] == 3


class TestSendAlertsJob:
    @pytest.mark.asyncio
    async def test_info_alert(self):
        result = await send_alerts({
            "alert_type": "milestone",
            "severity": "info",
            "message": "Track hit 1000 streams!",
            "tenant_id": "t1",
        })
        assert result["status"] == "ok"
        assert result["alerts_sent"] == 1
        assert "in_app" in result["channels"]

    @pytest.mark.asyncio
    async def test_warning_alert_sends_email(self):
        result = await send_alerts({
            "alert_type": "publish_failed",
            "severity": "warning",
            "message": "Post failed to publish",
            "tenant_id": "t1",
            "post_id": "p1",
        })
        assert result["alerts_sent"] == 2
        assert "email" in result["channels"]
        assert "in_app" in result["channels"]

    @pytest.mark.asyncio
    async def test_critical_alert_sends_all_channels(self):
        result = await send_alerts({
            "alert_type": "publish_failed",
            "severity": "critical",
            "message": "Post failed permanently",
            "tenant_id": "t1",
        })
        assert result["alerts_sent"] == 3
        assert "push" in result["channels"]
        assert "email" in result["channels"]
        assert "in_app" in result["channels"]

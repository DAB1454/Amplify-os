"""Tests for mock data generation."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from amplify.core.analytics.mock_data import (
    generate_campaign_metrics,
    generate_daily_metrics,
    generate_demo_dataset,
)


class TestGenerateDailyMetrics:
    def test_correct_number_of_days(self):
        metrics = generate_daily_metrics("p1", "instagram", date(2026, 1, 1), days=7)
        assert len(metrics) == 7

    def test_all_fields_present(self):
        metrics = generate_daily_metrics("p1", "instagram", date(2026, 1, 1), days=1)
        m = metrics[0]
        assert m["post_id"] == "p1"
        assert m["platform"] == "instagram"
        assert m["impressions"] > 0
        assert m["likes"] >= 0
        assert m["comments"] >= 0
        assert m["shares"] >= 0
        assert m["saves"] >= 0
        assert m["clicks"] >= 0
        assert 0 <= m["engagement_rate"] <= 1
        assert 0 <= m["click_through_rate"] <= 1

    def test_deterministic(self):
        a = generate_daily_metrics("p1", "instagram", date(2026, 1, 1), days=3)
        b = generate_daily_metrics("p1", "instagram", date(2026, 1, 1), days=3)
        assert a == b

    def test_different_posts_different_data(self):
        a = generate_daily_metrics("p1", "instagram", date(2026, 1, 1), days=1)
        b = generate_daily_metrics("p2", "instagram", date(2026, 1, 1), days=1)
        assert a[0]["impressions"] != b[0]["impressions"]

    def test_tiktok_has_higher_impressions(self):
        ig = generate_daily_metrics("p1", "instagram", date(2026, 1, 1), days=30)
        tt = generate_daily_metrics("p1", "tiktok", date(2026, 1, 1), days=30)
        avg_ig = sum(m["impressions"] for m in ig) / len(ig)
        avg_tt = sum(m["impressions"] for m in tt) / len(tt)
        # TikTok range is (1000, 50000) vs Instagram (500, 8000)
        assert avg_tt > avg_ig


class TestGenerateCampaignMetrics:
    def test_generates_for_all_posts(self):
        posts = [
            {"post_id": "p1", "platform": "instagram"},
            {"post_id": "p2", "platform": "tiktok"},
        ]
        metrics = generate_campaign_metrics("c1", posts, date(2026, 1, 1), days=7)
        assert len(metrics) == 14  # 2 posts x 7 days
        assert all(m["campaign_id"] == "c1" for m in metrics)

    def test_empty_posts(self):
        metrics = generate_campaign_metrics("c1", [], days=7)
        assert metrics == []


class TestGenerateDemoDataset:
    def test_structure(self):
        demo = generate_demo_dataset()
        assert "campaign_id" in demo
        assert "posts" in demo
        assert "daily_metrics" in demo
        assert "aggregated" in demo

    def test_has_8_posts(self):
        demo = generate_demo_dataset()
        assert len(demo["posts"]) == 8

    def test_has_14_days_per_post(self):
        demo = generate_demo_dataset()
        assert len(demo["daily_metrics"]) == 8 * 14  # 8 posts x 14 days

    def test_aggregated_totals(self):
        demo = generate_demo_dataset()
        for pid, agg in demo["aggregated"].items():
            assert agg["impressions"] > 0
            assert agg["likes"] >= 0

    def test_deterministic(self):
        a = generate_demo_dataset()
        b = generate_demo_dataset()
        assert a["daily_metrics"] == b["daily_metrics"]

    def test_all_platforms_represented(self):
        demo = generate_demo_dataset()
        platforms = {p["platform"] for p in demo["posts"]}
        assert platforms == {"instagram", "tiktok", "youtube"}

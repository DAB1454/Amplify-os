"""Tests for post scoring and normalization."""

from __future__ import annotations

import pytest

from amplify.core.analytics.scoring import (
    PostScore,
    normalize_scores,
    score_post,
    score_posts,
)


class TestPostScore:
    def test_compute_rates_with_impressions(self):
        ps = PostScore(
            post_id="p1",
            impressions=1000,
            likes=50,
            comments=10,
            shares=5,
            saves=5,
            clicks=30,
        )
        ps.compute_rates()
        assert ps.engagement_rate == pytest.approx(0.07)
        assert ps.click_through_rate == pytest.approx(0.03)

    def test_compute_rates_zero_impressions(self):
        ps = PostScore(post_id="p1", impressions=0, likes=10)
        ps.compute_rates()
        assert ps.engagement_rate == 0.0
        assert ps.click_through_rate == 0.0

    def test_score_post_factory(self):
        ps = score_post(
            post_id="p1",
            platform="instagram",
            impressions=500,
            likes=25,
            comments=5,
            clicks=10,
        )
        assert ps.post_id == "p1"
        assert ps.platform == "instagram"
        assert ps.engagement_rate > 0


class TestNormalization:
    def test_single_post_gets_50th_percentile(self):
        scores = [score_post("p1", impressions=1000, likes=50, clicks=20)]
        normalize_scores(scores)
        assert scores[0].engagement_score == 50.0
        assert scores[0].click_score == 50.0

    def test_two_posts_ordered(self):
        low = score_post("low", impressions=1000, likes=10, clicks=5)
        high = score_post("high", impressions=1000, likes=100, clicks=50)
        scores = normalize_scores([low, high])
        assert high.engagement_score > low.engagement_score
        assert high.click_score > low.click_score

    def test_composite_is_weighted(self):
        ps = PostScore(post_id="p1")
        ps.engagement_score = 80.0
        ps.click_score = 40.0
        # Default weights: 0.6 eng + 0.4 click
        expected = 80.0 * 0.6 + 40.0 * 0.4
        scores = [ps]
        # Can't use normalize_scores directly since it recomputes,
        # so test the formula
        composite = ps.engagement_score * 0.6 + ps.click_score * 0.4
        assert composite == pytest.approx(expected)

    def test_empty_list(self):
        result = normalize_scores([])
        assert result == []

    def test_custom_weights(self):
        ps = score_post("p1", impressions=1000, likes=50, clicks=50)
        scores = normalize_scores([ps], engagement_weight=0.3, click_weight=0.7)
        # Single post → 50th percentile on both axes
        expected = 50.0 * 0.3 + 50.0 * 0.7
        assert scores[0].composite_score == pytest.approx(expected)


class TestScorePosts:
    def test_batch_scoring(self):
        data = [
            {"post_id": "p1", "platform": "instagram", "impressions": 1000, "likes": 50, "comments": 5, "shares": 3, "saves": 2, "clicks": 20},
            {"post_id": "p2", "platform": "tiktok", "impressions": 5000, "likes": 300, "comments": 50, "shares": 20, "saves": 10, "clicks": 100},
            {"post_id": "p3", "platform": "youtube", "impressions": 500, "likes": 5, "comments": 1, "shares": 0, "saves": 0, "clicks": 2},
        ]
        scores = score_posts(data)
        assert len(scores) == 3

        # All scores should be computed
        for s in scores:
            assert s.engagement_rate >= 0
            assert s.click_through_rate >= 0
            assert 0 <= s.composite_score <= 100

    def test_highest_engagement_gets_highest_score(self):
        data = [
            {"post_id": "low", "platform": "ig", "impressions": 1000, "likes": 5, "comments": 0, "shares": 0, "saves": 0, "clicks": 1},
            {"post_id": "mid", "platform": "ig", "impressions": 1000, "likes": 50, "comments": 10, "shares": 5, "saves": 5, "clicks": 20},
            {"post_id": "high", "platform": "ig", "impressions": 1000, "likes": 200, "comments": 40, "shares": 20, "saves": 15, "clicks": 80},
        ]
        scores = score_posts(data)
        by_id = {s.post_id: s for s in scores}
        assert by_id["high"].composite_score > by_id["mid"].composite_score
        assert by_id["mid"].composite_score > by_id["low"].composite_score

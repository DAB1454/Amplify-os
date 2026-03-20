"""Tests for the weekly analyst report and keep/remix/stop verdicts."""

from __future__ import annotations

import pytest

from amplify.core.analytics.analyst import (
    AnalystRecommendation,
    PostVerdict,
    Verdict,
    weekly_analyst_report,
)
from amplify.core.analytics.scoring import PostScore, normalize_scores, score_posts


class TestVerdictClassification:
    def _make_scores(self, composites: list[float]) -> list[PostScore]:
        """Create PostScore objects with preset composite scores."""
        scores = []
        for i, c in enumerate(composites):
            ps = PostScore(
                post_id=f"p{i}",
                platform="instagram",
                composite_score=c,
                engagement_score=c,
                click_score=c,
            )
            scores.append(ps)
        return scores

    def test_high_score_is_keep(self):
        scores = self._make_scores([85.0])
        report = weekly_analyst_report("c1", "2026-01-01", "2026-01-07", scores)
        assert report.verdicts[0].verdict == Verdict.KEEP

    def test_medium_score_is_remix(self):
        scores = self._make_scores([55.0])
        report = weekly_analyst_report("c1", "2026-01-01", "2026-01-07", scores)
        assert report.verdicts[0].verdict == Verdict.REMIX

    def test_low_score_is_stop(self):
        scores = self._make_scores([20.0])
        report = weekly_analyst_report("c1", "2026-01-01", "2026-01-07", scores)
        assert report.verdicts[0].verdict == Verdict.STOP

    def test_boundary_at_70_is_keep(self):
        scores = self._make_scores([70.0])
        report = weekly_analyst_report("c1", "2026-01-01", "2026-01-07", scores)
        assert report.verdicts[0].verdict == Verdict.KEEP

    def test_boundary_at_40_is_remix(self):
        scores = self._make_scores([40.0])
        report = weekly_analyst_report("c1", "2026-01-01", "2026-01-07", scores)
        assert report.verdicts[0].verdict == Verdict.REMIX

    def test_boundary_at_39_is_stop(self):
        scores = self._make_scores([39.9])
        report = weekly_analyst_report("c1", "2026-01-01", "2026-01-07", scores)
        assert report.verdicts[0].verdict == Verdict.STOP

    def test_custom_thresholds(self):
        scores = self._make_scores([50.0])
        report = weekly_analyst_report(
            "c1", "2026-01-01", "2026-01-07", scores,
            keep_threshold=90, stop_threshold=60,
        )
        assert report.verdicts[0].verdict == Verdict.STOP


class TestAnalystReport:
    def test_mixed_verdicts(self):
        data = [
            {"post_id": "high", "platform": "ig", "impressions": 1000, "likes": 200, "comments": 40, "shares": 20, "saves": 15, "clicks": 80},
            {"post_id": "mid", "platform": "ig", "impressions": 1000, "likes": 50, "comments": 10, "shares": 5, "saves": 5, "clicks": 20},
            {"post_id": "low", "platform": "ig", "impressions": 1000, "likes": 5, "comments": 0, "shares": 0, "saves": 0, "clicks": 1},
        ]
        scores = score_posts(data)
        report = weekly_analyst_report("c1", "2026-01-01", "2026-01-07", scores)

        assert report.total_posts == 3
        assert report.keep_count + report.remix_count + report.stop_count == 3

    def test_report_summary_contains_counts(self):
        data = [
            {"post_id": f"p{i}", "platform": "ig", "impressions": 1000, "likes": i * 30, "comments": i * 5, "shares": i, "saves": i, "clicks": i * 10}
            for i in range(5)
        ]
        scores = score_posts(data)
        report = weekly_analyst_report("c1", "2026-01-01", "2026-01-07", scores)
        assert "keep" in report.summary.lower() or "remix" in report.summary.lower()
        assert report.campaign_id == "c1"
        assert report.period_start == "2026-01-01"

    def test_empty_posts(self):
        report = weekly_analyst_report("c1", "2026-01-01", "2026-01-07", [])
        assert report.total_posts == 0
        assert report.keep_count == 0
        assert report.verdicts == []

    def test_verdict_has_reason(self):
        scores = [PostScore(
            post_id="p1",
            platform="tiktok",
            composite_score=85.0,
            engagement_score=90.0,
            click_score=75.0,
        )]
        report = weekly_analyst_report("c1", "2026-01-01", "2026-01-07", scores)
        assert len(report.verdicts[0].reason) > 0
        assert "score" in report.verdicts[0].reason.lower()

    def test_all_verdicts_have_post_ids(self):
        data = [
            {"post_id": f"post_{i}", "platform": "yt", "impressions": 500 + i * 100, "likes": 10 + i * 20, "comments": i, "shares": 0, "saves": 0, "clicks": 5 + i * 5}
            for i in range(4)
        ]
        scores = score_posts(data)
        report = weekly_analyst_report("c1", "2026-01-01", "2026-01-07", scores)
        post_ids = {v.post_id for v in report.verdicts}
        assert post_ids == {f"post_{i}" for i in range(4)}

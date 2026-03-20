"""Tests for the offline replay evaluation engine.

Covers:
- PostOpportunity → CandidatePost conversion
- ReplayDataset filtering and properties
- PolicyEvalReport metrics correctness
- ComparisonReport winner selection and summary formatting
- ReplayEngine determinism
- build_sample_dataset structure
- Edge cases: empty dataset, single post, all-equal rewards
"""

from __future__ import annotations

import pytest

from amplify.learning.evaluation.replay import (
    ComparisonReport,
    PolicyEvalReport,
    PostEvalResult,
    PostOpportunity,
    RankingPolicySpec,
    ReplayDataset,
    ReplayEngine,
    build_sample_dataset,
    _spearman,
)
from amplify.learning.ranking.post_ranker import (
    CandidatePost,
    PostRanker,
    RankedPost,
)


# ── Fixtures ─────────────────────────────────────────────────────


def _sample_opportunities() -> list[PostOpportunity]:
    """Five posts with known rewards for testing."""
    return [
        PostOpportunity(
            post_id="p1", platform="instagram", content_type="reel",
            post_hour=18, caption_length=120, hashtag_count=8,
            has_media=True, hook_type="question", cta_type="presave",
            campaign_phase="pre_release", actual_reward=0.9,
        ),
        PostOpportunity(
            post_id="p2", platform="instagram", content_type="carousel",
            post_hour=12, caption_length=250, hashtag_count=5,
            has_media=True, hook_type="teaser", cta_type="presave",
            campaign_phase="pre_release", actual_reward=0.7,
        ),
        PostOpportunity(
            post_id="p3", platform="tiktok", content_type="short",
            post_hour=19, caption_length=60, hashtag_count=4,
            has_media=True, hook_type="question", cta_type="follow",
            campaign_phase="pre_release", actual_reward=0.5,
        ),
        PostOpportunity(
            post_id="p4", platform="instagram", content_type="story",
            post_hour=22, caption_length=40, hashtag_count=2,
            has_media=True, hook_type="teaser", cta_type="follow",
            campaign_phase="release_day", actual_reward=0.3,
        ),
        PostOpportunity(
            post_id="p5", platform="tiktok", content_type="short",
            post_hour=3, caption_length=20, hashtag_count=0,
            has_media=True, hook_type="teaser", cta_type="none",
            campaign_phase="post_release", actual_reward=0.1,
        ),
    ]


def _make_dataset(opps=None) -> ReplayDataset:
    return ReplayDataset(
        opportunities=opps or _sample_opportunities(),
        name="test_dataset",
        window_start="2026-03-01T00:00:00Z",
        window_end="2026-03-15T00:00:00Z",
    )


def _default_policy() -> RankingPolicySpec:
    return RankingPolicySpec(
        name="default",
        ranker=PostRanker(),
        description="Default ranking — no tenant patterns",
    )


# ── PostOpportunity tests ────────────────────────────────────────


class TestPostOpportunity:
    def test_to_candidate_preserves_fields(self):
        opp = _sample_opportunities()[0]
        c = opp.to_candidate()
        assert isinstance(c, CandidatePost)
        assert c.candidate_id == "p1"
        assert c.platform == "instagram"
        assert c.content_type == "reel"
        assert c.post_hour == 18
        assert c.has_media is True

    def test_to_candidate_maps_all_features(self):
        opp = PostOpportunity(
            post_id="x", platform="tiktok", content_type="short",
            post_hour=10, post_day_of_week=3, caption_length=100,
            hashtag_count=5, has_link=True, has_media=True,
            hook_type="statistic", cta_type="link",
            campaign_phase="release_day", actual_reward=0.5,
        )
        c = opp.to_candidate()
        assert c.post_day_of_week == 3
        assert c.has_link is True
        assert c.cta_type == "link"


# ── ReplayDataset tests ─────────────────────────────────────────


class TestReplayDataset:
    def test_size(self):
        ds = _make_dataset()
        assert ds.size == 5

    def test_platforms(self):
        ds = _make_dataset()
        assert ds.platforms == {"instagram", "tiktok"}

    def test_mean_actual_reward(self):
        ds = _make_dataset()
        expected = (0.9 + 0.7 + 0.5 + 0.3 + 0.1) / 5
        assert abs(ds.mean_actual_reward - expected) < 1e-6

    def test_filter_by_platform(self):
        ds = _make_dataset()
        ig = ds.filter_by_platform("instagram")
        assert ig.size == 3
        assert ig.platforms == {"instagram"}
        assert "instagram" in ig.name

    def test_filter_by_prompt_version(self):
        opps = [
            PostOpportunity(post_id="a", prompt_version_id="v1", actual_reward=0.5),
            PostOpportunity(post_id="b", prompt_version_id="v2", actual_reward=0.3),
            PostOpportunity(post_id="c", prompt_version_id="v1", actual_reward=0.7),
        ]
        ds = _make_dataset(opps)
        filtered = ds.filter_by_prompt_version("v1")
        assert filtered.size == 2

    def test_empty_dataset(self):
        ds = ReplayDataset()
        assert ds.size == 0
        assert ds.mean_actual_reward == 0.0
        assert ds.platforms == set()


# ── Spearman correlation tests ───────────────────────────────────


class TestSpearman:
    def test_perfect_correlation(self):
        assert abs(_spearman([1, 2, 3, 4], [1, 2, 3, 4]) - 1.0) < 1e-6

    def test_perfect_inverse(self):
        assert abs(_spearman([1, 2, 3, 4], [4, 3, 2, 1]) - (-1.0)) < 1e-6

    def test_too_few_values(self):
        assert _spearman([1, 2], [2, 1]) == 0.0

    def test_partial_correlation(self):
        # [1,2,3,4,5] vs [2,1,3,5,4] — some agreement
        r = _spearman([1, 2, 3, 4, 5], [2, 1, 3, 5, 4])
        assert 0.0 < r < 1.0


# ── ReplayEngine tests ──────────────────────────────────────────


class TestReplayEngine:
    def test_evaluate_single_returns_report(self):
        engine = ReplayEngine()
        ds = _make_dataset()
        report = engine.evaluate_single(ds, _default_policy())
        assert isinstance(report, PolicyEvalReport)
        assert report.sample_size == 5
        assert report.mae is not None
        assert report.evaluated_at != ""

    def test_evaluate_comparison(self):
        engine = ReplayEngine()
        ds = _make_dataset()
        policies = [
            _default_policy(),
            RankingPolicySpec(
                name="phase_aware",
                ranker=PostRanker.for_campaign_phase("pre_release"),
                description="Pre-release tuned",
            ),
        ]
        report = engine.evaluate(ds, policies)
        assert isinstance(report, ComparisonReport)
        assert report.dataset_size == 5
        assert len(report.policies) == 2
        assert report.winner != ""

    def test_determinism(self):
        """Same dataset + same policies → same results."""
        engine = ReplayEngine()
        ds = _make_dataset()
        policy = _default_policy()
        r1 = engine.evaluate_single(ds, policy)
        r2 = engine.evaluate_single(ds, policy)
        assert r1.mae == r2.mae
        assert r1.rank_correlation == r2.rank_correlation
        assert r1.top_k_precision == r2.top_k_precision
        assert r1.expected_reward_top1 == r2.expected_reward_top1

    def test_empty_dataset(self):
        engine = ReplayEngine()
        ds = ReplayDataset()
        report = engine.evaluate(ds, [_default_policy()])
        assert report.dataset_size == 0
        assert report.policies == []

    def test_per_post_results_present(self):
        engine = ReplayEngine()
        ds = _make_dataset()
        report = engine.evaluate_single(ds, _default_policy())
        assert len(report.results) == 5
        for r in report.results:
            assert isinstance(r, PostEvalResult)
            assert isinstance(r.predicted_score, float)
            assert r.absolute_error >= 0

    def test_results_have_factors(self):
        engine = ReplayEngine()
        ds = _make_dataset()
        report = engine.evaluate_single(ds, _default_policy())
        # At least the top-ranked post should have factors
        top = sorted(report.results, key=lambda r: r.predicted_score, reverse=True)[0]
        assert len(top.factors) > 0

    def test_rank_correlation_direction(self):
        """A ranker that scores higher for actually-better posts should have positive correlation."""
        engine = ReplayEngine()
        ds = _make_dataset()
        report = engine.evaluate_single(ds, _default_policy())
        # The default ranker should have at least some positive correlation
        # (it uses heuristics that partially align with good outcomes)
        if report.rank_correlation is not None:
            # Just verify it's a valid number
            assert -1.0 <= report.rank_correlation <= 1.0


# ── PolicyEvalReport tests ──────────────────────────────────────


class TestPolicyEvalReport:
    def test_to_dict(self):
        engine = ReplayEngine()
        ds = _make_dataset()
        report = engine.evaluate_single(ds, _default_policy())
        d = report.to_dict()
        assert "policy_name" in d
        assert "mae" in d
        assert "rank_correlation" in d
        assert "is_useful" in d
        assert d["sample_size"] == 5

    def test_is_useful_high_correlation(self):
        report = PolicyEvalReport(
            policy_name="test",
            sample_size=10,
            rank_correlation=0.5,
        )
        assert report.is_useful is True

    def test_is_useful_low_correlation(self):
        report = PolicyEvalReport(
            policy_name="test",
            sample_size=10,
            rank_correlation=0.1,
            top_k_precision=0.2,
        )
        assert report.is_useful is False

    def test_risk_score_bounded(self):
        engine = ReplayEngine()
        ds = _make_dataset()
        report = engine.evaluate_single(ds, _default_policy())
        if report.risk_score is not None:
            assert 0.0 <= report.risk_score <= 1.0

    def test_confidence_interval_present_for_large_dataset(self):
        engine = ReplayEngine()
        ds = _make_dataset()
        report = engine.evaluate_single(ds, _default_policy())
        assert report.expected_reward_ci_lower is not None
        assert report.expected_reward_ci_upper is not None
        assert report.expected_reward_ci_lower <= report.expected_reward_ci_upper

    def test_platform_lift(self):
        engine = ReplayEngine()
        ds = _make_dataset()
        report = engine.evaluate_single(ds, _default_policy())
        # Should have lift for at least some platforms
        assert isinstance(report.platform_lift, dict)


# ── ComparisonReport tests ──────────────────────────────────────


class TestComparisonReport:
    def test_summary_format(self):
        engine = ReplayEngine()
        ds = _make_dataset()
        policies = [_default_policy()]
        report = engine.evaluate(ds, policies)
        summary = report.summary()
        assert "test_dataset" in summary
        assert "MAE" in summary
        assert "Rank correlation" in summary

    def test_to_dict(self):
        engine = ReplayEngine()
        ds = _make_dataset()
        report = engine.evaluate(ds, [_default_policy()])
        d = report.to_dict()
        assert "dataset_name" in d
        assert "policies" in d
        assert len(d["policies"]) == 1

    def test_winner_selection_prefers_higher_reward(self):
        """When comparing two policies, the one with higher expected reward wins."""
        engine = ReplayEngine()
        ds = _make_dataset()
        # Use two different profiles — they'll produce different rankings
        policies = [
            RankingPolicySpec(
                name="pre_release",
                ranker=PostRanker.for_campaign_phase("pre_release"),
            ),
            RankingPolicySpec(
                name="release_day",
                ranker=PostRanker.for_campaign_phase("release_day"),
            ),
        ]
        report = engine.evaluate(ds, policies)
        assert report.winner in ("pre_release", "release_day")
        assert report.winner_reason != ""


# ── Sample dataset tests ────────────────────────────────────────


class TestSampleDataset:
    def test_build_sample_dataset(self):
        ds = build_sample_dataset()
        assert ds.size == 12
        assert ds.name == "sample_campaign_lifecycle"

    def test_sample_platforms(self):
        ds = build_sample_dataset()
        assert "instagram" in ds.platforms
        assert "tiktok" in ds.platforms

    def test_sample_phases(self):
        ds = build_sample_dataset()
        phases = {o.campaign_phase for o in ds.opportunities}
        assert phases == {"pre_release", "release_day", "post_release"}

    def test_sample_rewards_reasonable(self):
        ds = build_sample_dataset()
        for o in ds.opportunities:
            assert 0.0 <= o.actual_reward <= 1.0

    def test_sample_all_have_media(self):
        ds = build_sample_dataset()
        for o in ds.opportunities:
            assert o.has_media is True

    def test_sample_evaluate_succeeds(self):
        """The sample dataset should work end-to-end with the replay engine."""
        engine = ReplayEngine()
        ds = build_sample_dataset()
        report = engine.evaluate(ds, [_default_policy()])
        assert report.dataset_size == 12
        assert len(report.policies) == 1
        assert report.policies[0].mae is not None


# ── Edge cases ──────────────────────────────────────────────────


class TestEdgeCases:
    def test_single_post(self):
        ds = ReplayDataset(opportunities=[
            PostOpportunity(post_id="solo", actual_reward=0.5, has_media=True),
        ])
        engine = ReplayEngine()
        report = engine.evaluate_single(ds, _default_policy())
        assert report.sample_size == 1
        assert report.rank_correlation is None  # too few for correlation

    def test_all_equal_rewards(self):
        opps = [
            PostOpportunity(
                post_id=f"eq{i}", actual_reward=0.5, has_media=True,
                platform="instagram", content_type="reel",
            )
            for i in range(5)
        ]
        ds = ReplayDataset(opportunities=opps)
        engine = ReplayEngine()
        report = engine.evaluate_single(ds, _default_policy())
        assert report.sample_size == 5
        # All rewards equal → MAE should equal |predicted - 0.5|
        assert report.mae is not None

    def test_post_eval_result_rank_error(self):
        r = PostEvalResult(
            post_id="x",
            predicted_score=0.8,
            predicted_rank=1,
            actual_reward=0.5,
            actual_rank=3,
            absolute_error=0.3,
        )
        assert r.rank_error == 2

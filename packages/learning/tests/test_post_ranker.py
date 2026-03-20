"""Tests for the rule-based post ranking engine.

Covers:
- Scoring and ranking mechanics
- Explanation transparency and decomposability
- All three ranking profiles (pre_release, release_day, post_release)
- Pattern matching (winning/losing)
- Heuristic bonuses (CTA alignment, format, timing)
- Policy penalties (missing CTA, losing patterns, no media, hashtags)
- Edge cases (no patterns, empty candidates, ties)
- Three full candidate-set examples
"""

from __future__ import annotations

import pytest

from amplify.learning.ranking.post_ranker import (
    CandidatePost,
    PatternSignal,
    PostRanker,
    RankedPost,
    ScoreFactor,
    _caption_bucket,
    _hashtag_bucket,
)


# ── Helpers ──────────────────────────────────────────────────────


def _winning(feature: str, value: str, confidence: float = 0.85, lift: float = 0.30) -> PatternSignal:
    return PatternSignal(
        pattern_type="content",
        subject_feature=feature,
        subject_value=value,
        direction="winning",
        confidence=confidence,
        lift_vs_baseline=lift,
    )


def _losing(feature: str, value: str, confidence: float = 0.80, lift: float = -0.25) -> PatternSignal:
    return PatternSignal(
        pattern_type="content",
        subject_feature=feature,
        subject_value=value,
        direction="losing",
        confidence=confidence,
        lift_vs_baseline=lift,
    )


STANDARD_PATTERNS = [
    _winning("content_type", "reel", confidence=0.90, lift=0.40),
    _winning("post_hour", "18", confidence=0.85, lift=0.25),
    _winning("cta_type", "presave", confidence=0.80, lift=0.35),
    _losing("content_type", "story", confidence=0.75, lift=-0.20),
    _losing("post_hour", "3", confidence=0.70, lift=-0.30),
    _winning("hook_type", "question", confidence=0.75, lift=0.20),
    _losing("hashtag_count_bucket", "many", confidence=0.65, lift=-0.15),
]


# ═══════════════════════════════════════════════════════════════════
# Bucket helpers
# ═══════════════════════════════════════════════════════════════════


class TestBuckets:
    def test_caption_buckets(self):
        assert _caption_bucket(10) == "short"
        assert _caption_bucket(100) == "medium"
        assert _caption_bucket(200) == "long"
        assert _caption_bucket(500) == "very_long"

    def test_hashtag_buckets(self):
        assert _hashtag_bucket(0) == "none"
        assert _hashtag_bucket(3) == "few"
        assert _hashtag_bucket(10) == "moderate"
        assert _hashtag_bucket(25) == "many"


# ═══════════════════════════════════════════════════════════════════
# CandidatePost
# ═══════════════════════════════════════════════════════════════════


class TestCandidatePost:
    def test_to_features_includes_present_fields(self):
        c = CandidatePost(
            candidate_id="c1",
            platform="instagram",
            content_type="reel",
            post_hour=18,
            has_link=True,
        )
        feats = c.to_features()
        assert feats["platform"] == "instagram"
        assert feats["content_type"] == "reel"
        assert feats["post_hour"] == 18
        assert feats["has_link"] == "True"

    def test_to_features_omits_none_fields(self):
        c = CandidatePost(candidate_id="c2", platform="tiktok")
        feats = c.to_features()
        assert "post_hour" not in feats
        assert "caption_length_bucket" not in feats

    def test_caption_length_becomes_bucket(self):
        c = CandidatePost(candidate_id="c3", caption_length=120)
        assert c.to_features()["caption_length_bucket"] == "medium"

    def test_extra_fields_included(self):
        c = CandidatePost(candidate_id="c4", extra={"custom_tag": "promo"})
        assert c.to_features()["custom_tag"] == "promo"


# ═══════════════════════════════════════════════════════════════════
# Basic ranking mechanics
# ═══════════════════════════════════════════════════════════════════


class TestRankingBasics:
    def test_empty_candidates_returns_empty(self):
        ranker = PostRanker(patterns=STANDARD_PATTERNS)
        assert ranker.rank([]) == []

    def test_single_candidate_gets_rank_1(self):
        ranker = PostRanker(patterns=STANDARD_PATTERNS, profile="post_release")
        results = ranker.rank([CandidatePost(candidate_id="only")])
        assert len(results) == 1
        assert results[0].rank == 1

    def test_higher_score_gets_lower_rank(self):
        ranker = PostRanker(patterns=STANDARD_PATTERNS, profile="pre_release")
        good = CandidatePost(candidate_id="good", content_type="reel", post_hour=18, cta_type="presave")
        bad = CandidatePost(candidate_id="bad", content_type="story", post_hour=3, cta_type="none")
        results = ranker.rank([bad, good])
        assert results[0].candidate_id == "good"
        assert results[1].candidate_id == "bad"
        assert results[0].score > results[1].score

    def test_deterministic_ordering(self):
        """Same inputs produce same ordering every time."""
        ranker = PostRanker(patterns=STANDARD_PATTERNS, profile="release_day")
        candidates = [
            CandidatePost(candidate_id="a", content_type="reel", post_hour=18),
            CandidatePost(candidate_id="b", content_type="post", post_hour=12),
            CandidatePost(candidate_id="c", content_type="story", post_hour=3),
        ]
        r1 = ranker.rank(candidates)
        r2 = ranker.rank(candidates)
        assert [r.candidate_id for r in r1] == [r.candidate_id for r in r2]

    def test_tie_broken_by_candidate_id(self):
        """When scores are equal, candidate_id is the tiebreaker."""
        ranker = PostRanker(patterns=[], profile="post_release")
        a = CandidatePost(candidate_id="alpha")
        b = CandidatePost(candidate_id="beta")
        results = ranker.rank([b, a])
        # Both have same heuristic/policy scores (no features)
        # alpha < beta alphabetically, so alpha comes first on tie
        assert results[0].candidate_id == "alpha"


# ═══════════════════════════════════════════════════════════════════
# Score decomposability (every score = sum of named factors)
# ═══════════════════════════════════════════════════════════════════


class TestDecomposability:
    def test_score_equals_sum_of_factors(self):
        ranker = PostRanker(patterns=STANDARD_PATTERNS, profile="pre_release")
        c = CandidatePost(
            candidate_id="x",
            content_type="reel",
            post_hour=18,
            cta_type="presave",
            platform="instagram",
        )
        results = ranker.rank([c])
        r = results[0]
        factor_sum = sum(f.value for f in r.factors)
        assert abs(r.score - factor_sum) < 1e-6

    def test_every_factor_has_source(self):
        ranker = PostRanker(patterns=STANDARD_PATTERNS, profile="release_day")
        c = CandidatePost(candidate_id="y", content_type="story", cta_type="none")
        results = ranker.rank([c])
        for f in results[0].factors:
            assert f.source in ("pattern", "heuristic", "policy")

    def test_factor_value_equals_raw_times_weight(self):
        ranker = PostRanker(patterns=STANDARD_PATTERNS, profile="post_release")
        c = CandidatePost(candidate_id="z", content_type="reel", post_hour=18)
        results = ranker.rank([c])
        for f in results[0].factors:
            if f.source == "pattern":
                assert abs(f.value - f.raw * f.weight) < 1e-6


# ═══════════════════════════════════════════════════════════════════
# Explanation tests
# ═══════════════════════════════════════════════════════════════════


class TestExplanations:
    def test_explanation_includes_rank_and_score(self):
        ranker = PostRanker(patterns=STANDARD_PATTERNS, profile="pre_release")
        c = CandidatePost(candidate_id="e1", content_type="reel")
        r = ranker.rank([c])[0]
        explanation = r.explanation
        assert "Rank #1" in explanation
        assert "score" in explanation

    def test_explanation_shows_top_factors(self):
        ranker = PostRanker(patterns=STANDARD_PATTERNS, profile="pre_release")
        c = CandidatePost(candidate_id="e2", content_type="reel", cta_type="presave")
        r = ranker.rank([c])[0]
        explanation = r.explanation
        assert "Winning pattern" in explanation or "CTA" in explanation

    def test_explanation_shows_warnings(self):
        ranker = PostRanker(patterns=STANDARD_PATTERNS, profile="pre_release")
        c = CandidatePost(candidate_id="e3", cta_type="none")
        r = ranker.rank([c])[0]
        assert "Warnings" in r.explanation
        assert "no CTA" in r.explanation.lower() or "No CTA" in r.explanation

    def test_to_dict_roundtrip(self):
        ranker = PostRanker(patterns=STANDARD_PATTERNS, profile="release_day")
        c = CandidatePost(candidate_id="e4", content_type="reel", post_hour=18)
        r = ranker.rank([c])[0]
        d = r.to_dict()
        assert d["candidate_id"] == "e4"
        assert d["rank"] == 1
        assert isinstance(d["score"], float)
        assert isinstance(d["factors"], list)
        assert all("source" in f for f in d["factors"])
        assert all("detail" in f for f in d["factors"])

    def test_factor_detail_mentions_feature_value(self):
        ranker = PostRanker(patterns=STANDARD_PATTERNS, profile="post_release")
        c = CandidatePost(candidate_id="e5", content_type="reel")
        r = ranker.rank([c])[0]
        pattern_factors = [f for f in r.factors if f.source == "pattern"]
        assert any("reel" in f.detail for f in pattern_factors)


# ═══════════════════════════════════════════════════════════════════
# Pattern matching
# ═══════════════════════════════════════════════════════════════════


class TestPatternMatching:
    def test_winning_pattern_adds_positive_score(self):
        ranker = PostRanker(
            patterns=[_winning("content_type", "reel", confidence=0.90, lift=0.40)],
            profile="post_release",
        )
        c = CandidatePost(candidate_id="p1", content_type="reel")
        r = ranker.rank([c])[0]
        pattern_factors = [f for f in r.factors if f.source == "pattern"]
        assert len(pattern_factors) == 1
        assert pattern_factors[0].value > 0

    def test_losing_pattern_adds_negative_score(self):
        ranker = PostRanker(
            patterns=[_losing("content_type", "story", confidence=0.80, lift=-0.20)],
            profile="post_release",
        )
        c = CandidatePost(candidate_id="p2", content_type="story")
        r = ranker.rank([c])[0]
        pattern_factors = [f for f in r.factors if f.source == "pattern"]
        assert len(pattern_factors) == 1
        assert pattern_factors[0].value < 0

    def test_no_match_produces_warning(self):
        ranker = PostRanker(
            patterns=[_winning("content_type", "reel")],
            profile="post_release",
        )
        c = CandidatePost(candidate_id="p3", platform="instagram")  # no content_type
        r = ranker.rank([c])[0]
        assert any("No patterns matched" in w for w in r.warnings)

    def test_higher_confidence_pattern_scores_higher(self):
        high = _winning("content_type", "reel", confidence=0.95, lift=0.30)
        low = _winning("content_type", "post", confidence=0.50, lift=0.30)
        ranker = PostRanker(patterns=[high, low], profile="post_release")

        c_reel = CandidatePost(candidate_id="reel", content_type="reel")
        c_post = CandidatePost(candidate_id="post", content_type="post")
        results = ranker.rank([c_post, c_reel])
        assert results[0].candidate_id == "reel"

    def test_pinned_pattern_respected(self):
        """Pinned patterns are included in scoring like any other."""
        pinned = PatternSignal(
            pattern_type="content",
            subject_feature="content_type",
            subject_value="carousel",
            direction="winning",
            confidence=1.0,
            lift_vs_baseline=0.50,
            is_pinned=True,
        )
        ranker = PostRanker(patterns=[pinned], profile="post_release")
        c = CandidatePost(candidate_id="pin", content_type="carousel")
        r = ranker.rank([c])[0]
        pattern_factors = [f for f in r.factors if f.source == "pattern"]
        assert len(pattern_factors) == 1
        assert pattern_factors[0].value > 0

    def test_both_winning_and_losing_on_different_features(self):
        """A candidate can match winning on one feature and losing on another."""
        patterns = [
            _winning("content_type", "reel", confidence=0.90, lift=0.40),
            _losing("post_hour", "3", confidence=0.80, lift=-0.30),
        ]
        ranker = PostRanker(patterns=patterns, profile="post_release")
        c = CandidatePost(candidate_id="mixed", content_type="reel", post_hour=3)
        r = ranker.rank([c])[0]
        pattern_factors = [f for f in r.factors if f.source == "pattern"]
        positive = [f for f in pattern_factors if f.value > 0]
        negative = [f for f in pattern_factors if f.value < 0]
        assert len(positive) >= 1
        assert len(negative) >= 1


# ═══════════════════════════════════════════════════════════════════
# Heuristic bonuses
# ═══════════════════════════════════════════════════════════════════


class TestHeuristics:
    def test_cta_alignment_bonus_pre_release(self):
        ranker = PostRanker(patterns=[], profile="pre_release")
        c = CandidatePost(candidate_id="h1", cta_type="presave")
        r = ranker.rank([c])[0]
        cta_factors = [f for f in r.factors if f.name == "cta_alignment"]
        assert len(cta_factors) == 1
        assert cta_factors[0].value > 0

    def test_cta_misalignment_penalty(self):
        ranker = PostRanker(patterns=[], profile="release_day")
        c = CandidatePost(candidate_id="h2", cta_type="presave")  # not ideal for release_day
        r = ranker.rank([c])[0]
        mis_factors = [f for f in r.factors if f.name == "cta_misalignment"]
        assert len(mis_factors) == 1
        assert mis_factors[0].value < 0

    def test_content_format_bonus(self):
        ranker = PostRanker(patterns=[], profile="pre_release")
        c = CandidatePost(candidate_id="h3", content_type="reel")
        r = ranker.rank([c])[0]
        fmt_factors = [f for f in r.factors if f.name == "content_format_bonus"]
        assert len(fmt_factors) == 1
        assert fmt_factors[0].value > 0

    def test_prime_timing_bonus(self):
        ranker = PostRanker(patterns=[], profile="release_day")
        c = CandidatePost(candidate_id="h4", post_hour=18)
        r = ranker.rank([c])[0]
        timing_factors = [f for f in r.factors if f.name == "timing_prime_hours"]
        assert len(timing_factors) == 1
        assert timing_factors[0].value > 0

    def test_no_timing_bonus_at_3am(self):
        ranker = PostRanker(patterns=[], profile="release_day")
        c = CandidatePost(candidate_id="h5", post_hour=3)
        r = ranker.rank([c])[0]
        timing_factors = [f for f in r.factors if f.name == "timing_prime_hours"]
        assert len(timing_factors) == 0


# ═══════════════════════════════════════════════════════════════════
# Policy constraints
# ═══════════════════════════════════════════════════════════════════


class TestPolicyConstraints:
    def test_missing_cta_penalty(self):
        ranker = PostRanker(patterns=[], profile="release_day")
        c = CandidatePost(candidate_id="pol1", content_type="reel")
        r = ranker.rank([c])[0]
        cta_penalty = [f for f in r.factors if f.name == "missing_cta"]
        assert len(cta_penalty) == 1
        assert cta_penalty[0].value < 0

    def test_cta_none_string_also_penalized(self):
        ranker = PostRanker(patterns=[], profile="post_release")
        c = CandidatePost(candidate_id="pol2", cta_type="none")
        r = ranker.rank([c])[0]
        cta_penalty = [f for f in r.factors if f.name == "missing_cta"]
        assert len(cta_penalty) == 1

    def test_no_media_penalty_visual_platform(self):
        ranker = PostRanker(patterns=[], profile="post_release")
        c = CandidatePost(candidate_id="pol3", platform="instagram", has_media=False)
        r = ranker.rank([c])[0]
        media_penalty = [f for f in r.factors if f.name == "no_media"]
        assert len(media_penalty) == 1
        assert media_penalty[0].value < 0

    def test_no_media_penalty_not_for_email(self):
        ranker = PostRanker(patterns=[], profile="post_release")
        c = CandidatePost(candidate_id="pol4", platform="email", has_media=False)
        r = ranker.rank([c])[0]
        media_penalty = [f for f in r.factors if f.name == "no_media"]
        assert len(media_penalty) == 0

    def test_excessive_hashtags_penalty(self):
        ranker = PostRanker(patterns=[], profile="post_release")
        c = CandidatePost(candidate_id="pol5", hashtag_count=25)
        r = ranker.rank([c])[0]
        hash_penalty = [f for f in r.factors if f.name == "excessive_hashtags"]
        assert len(hash_penalty) == 1
        assert hash_penalty[0].value < 0

    def test_moderate_hashtags_no_penalty(self):
        ranker = PostRanker(patterns=[], profile="post_release")
        c = CandidatePost(candidate_id="pol6", hashtag_count=10)
        r = ranker.rank([c])[0]
        hash_penalty = [f for f in r.factors if f.name == "excessive_hashtags"]
        assert len(hash_penalty) == 0

    def test_multiple_losing_patterns_compound_penalty(self):
        patterns = [
            _losing("content_type", "story"),
            _losing("post_hour", "3"),
        ]
        ranker = PostRanker(patterns=patterns, profile="post_release")
        c = CandidatePost(candidate_id="pol7", content_type="story", post_hour=3)
        r = ranker.rank([c])[0]
        compound = [f for f in r.factors if f.name == "multiple_losing_patterns"]
        assert len(compound) == 1
        assert compound[0].value < 0


# ═══════════════════════════════════════════════════════════════════
# Ranking profiles
# ═══════════════════════════════════════════════════════════════════


class TestProfiles:
    def test_pre_release_favors_presave_cta(self):
        ranker = PostRanker(patterns=[], profile="pre_release")
        presave = CandidatePost(candidate_id="pr1", cta_type="presave", content_type="reel")
        link = CandidatePost(candidate_id="pr2", cta_type="link", content_type="reel")
        results = ranker.rank([link, presave])
        assert results[0].candidate_id == "pr1"

    def test_release_day_favors_link_cta(self):
        ranker = PostRanker(patterns=[], profile="release_day")
        link = CandidatePost(candidate_id="rd1", cta_type="link", content_type="reel")
        presave = CandidatePost(candidate_id="rd2", cta_type="presave", content_type="reel")
        results = ranker.rank([presave, link])
        assert results[0].candidate_id == "rd1"

    def test_phase_aliases_resolve_correctly(self):
        """e.g., 'countdown' maps to pre_release profile."""
        ranker = PostRanker(patterns=[], profile="countdown")
        assert ranker._profile == "pre_release"

    def test_unknown_profile_falls_back_to_post_release(self):
        ranker = PostRanker(patterns=[], profile="nonexistent_phase")
        assert ranker._profile == "post_release"

    def test_sustain_maps_to_post_release(self):
        ranker = PostRanker(patterns=[], profile="sustain")
        assert ranker._profile == "post_release"

    def test_release_day_weights_timing_higher(self):
        """Release day should weight timing more than post_release."""
        rd_ranker = PostRanker(patterns=[], profile="release_day")
        pr_ranker = PostRanker(patterns=[], profile="post_release")
        assert rd_ranker._feature_weights["post_hour"] > pr_ranker._feature_weights["post_hour"]


# ═══════════════════════════════════════════════════════════════════
# Constructor variants
# ═══════════════════════════════════════════════════════════════════


class TestConstructors:
    def test_from_pattern_dicts(self):
        dicts = [
            {
                "pattern_type": "timing",
                "subject_feature": "post_hour",
                "subject_value": "18",
                "direction": "winning",
                "confidence": 0.85,
                "evidence": {"lift_vs_baseline": 0.25},
            },
        ]
        ranker = PostRanker.from_pattern_dicts(dicts, profile="pre_release")
        c = CandidatePost(candidate_id="d1", post_hour=18)
        r = ranker.rank([c])[0]
        pattern_factors = [f for f in r.factors if f.source == "pattern"]
        assert len(pattern_factors) >= 1

    def test_from_pattern_dicts_with_flat_lift(self):
        """Support lift_vs_baseline at top level (not nested in evidence)."""
        dicts = [
            {
                "pattern_type": "content",
                "subject_feature": "content_type",
                "subject_value": "reel",
                "direction": "winning",
                "confidence": 0.90,
                "lift_vs_baseline": 0.40,
            },
        ]
        ranker = PostRanker.from_pattern_dicts(dicts, profile="post_release")
        c = CandidatePost(candidate_id="d2", content_type="reel")
        r = ranker.rank([c])[0]
        pattern_factors = [f for f in r.factors if f.source == "pattern"]
        assert pattern_factors[0].raw > 0

    def test_for_campaign_phase(self):
        ranker = PostRanker.for_campaign_phase("release_day")
        assert ranker._profile == "release_day"

    def test_custom_weights_override(self):
        ranker = PostRanker(
            patterns=STANDARD_PATTERNS,
            profile="post_release",
            custom_weights={"content_type": 5.0},
        )
        assert ranker._feature_weights["content_type"] == 5.0


# ═══════════════════════════════════════════════════════════════════
# No-pattern ranking (heuristic-only)
# ═══════════════════════════════════════════════════════════════════


class TestNoPatterns:
    def test_works_without_patterns(self):
        ranker = PostRanker(patterns=[], profile="pre_release")
        c = CandidatePost(candidate_id="np1", cta_type="presave", content_type="reel", post_hour=18)
        results = ranker.rank([c])
        assert len(results) == 1
        assert results[0].score > 0  # heuristics still contribute

    def test_heuristics_alone_differentiate(self):
        ranker = PostRanker(patterns=[], profile="pre_release")
        good = CandidatePost(candidate_id="np2", cta_type="presave", content_type="reel", post_hour=18)
        bad = CandidatePost(candidate_id="np3", cta_type="none", post_hour=3)
        results = ranker.rank([bad, good])
        assert results[0].candidate_id == "np2"
        assert results[0].score > results[1].score


# ═══════════════════════════════════════════════════════════════════
# Example candidate sets (integration-style tests)
# ═══════════════════════════════════════════════════════════════════


class TestExamplePreReleaseCampaign:
    """Example 1: Pre-release campaign — choosing between three teaser variants."""

    PATTERNS = [
        _winning("content_type", "reel", confidence=0.90, lift=0.40),
        _winning("hook_type", "question", confidence=0.80, lift=0.20),
        _winning("cta_type", "presave", confidence=0.85, lift=0.35),
        _losing("content_type", "story", confidence=0.70, lift=-0.15),
    ]

    CANDIDATES = [
        CandidatePost(
            candidate_id="teaser_reel",
            platform="instagram",
            content_type="reel",
            post_hour=18,
            hook_type="question",
            cta_type="presave",
            has_media=True,
            caption_length=120,
            hashtag_count=8,
        ),
        CandidatePost(
            candidate_id="teaser_carousel",
            platform="instagram",
            content_type="carousel",
            post_hour=12,
            hook_type="teaser",
            cta_type="presave",
            has_media=True,
            caption_length=200,
            hashtag_count=5,
        ),
        CandidatePost(
            candidate_id="teaser_story",
            platform="instagram",
            content_type="story",
            post_hour=22,
            hook_type="teaser",
            cta_type="follow",
            has_media=True,
            caption_length=50,
            hashtag_count=2,
        ),
    ]

    def test_reel_wins_pre_release(self):
        ranker = PostRanker(patterns=self.PATTERNS, profile="pre_release")
        results = ranker.rank(self.CANDIDATES)
        assert results[0].candidate_id == "teaser_reel"

    def test_story_ranks_last(self):
        ranker = PostRanker(patterns=self.PATTERNS, profile="pre_release")
        results = ranker.rank(self.CANDIDATES)
        assert results[-1].candidate_id == "teaser_story"

    def test_all_three_ranked(self):
        ranker = PostRanker(patterns=self.PATTERNS, profile="pre_release")
        results = ranker.rank(self.CANDIDATES)
        assert len(results) == 3
        assert {r.rank for r in results} == {1, 2, 3}

    def test_scores_are_decomposable(self):
        ranker = PostRanker(patterns=self.PATTERNS, profile="pre_release")
        for r in ranker.rank(self.CANDIDATES):
            assert abs(r.score - sum(f.value for f in r.factors)) < 1e-6

    def test_explanation_readable(self):
        ranker = PostRanker(patterns=self.PATTERNS, profile="pre_release")
        results = ranker.rank(self.CANDIDATES)
        for r in results:
            exp = r.explanation
            assert "Rank #" in exp
            assert "score" in exp


class TestExampleReleaseDayCampaign:
    """Example 2: Release day — maximize clicks to streaming link."""

    PATTERNS = [
        _winning("content_type", "reel", confidence=0.88, lift=0.35),
        _winning("post_hour", "12", confidence=0.82, lift=0.20),
        _winning("cta_type", "link", confidence=0.90, lift=0.45),
        _winning("has_link", "True", confidence=0.85, lift=0.30),
        _losing("post_hour", "23", confidence=0.75, lift=-0.20),
    ]

    CANDIDATES = [
        CandidatePost(
            candidate_id="midday_reel",
            platform="instagram",
            content_type="reel",
            post_hour=12,
            cta_type="link",
            has_link=True,
            has_media=True,
            caption_length=80,
        ),
        CandidatePost(
            candidate_id="evening_post",
            platform="instagram",
            content_type="post",
            post_hour=19,
            cta_type="link",
            has_link=True,
            has_media=True,
            caption_length=250,
        ),
        CandidatePost(
            candidate_id="late_story",
            platform="instagram",
            content_type="story",
            post_hour=23,
            cta_type="follow",
            has_link=False,
            has_media=True,
            caption_length=40,
        ),
    ]

    def test_midday_reel_wins_release_day(self):
        ranker = PostRanker(patterns=self.PATTERNS, profile="release_day")
        results = ranker.rank(self.CANDIDATES)
        assert results[0].candidate_id == "midday_reel"

    def test_late_story_ranks_last(self):
        ranker = PostRanker(patterns=self.PATTERNS, profile="release_day")
        results = ranker.rank(self.CANDIDATES)
        assert results[-1].candidate_id == "late_story"

    def test_link_cta_contributes_to_winner(self):
        ranker = PostRanker(patterns=self.PATTERNS, profile="release_day")
        results = ranker.rank(self.CANDIDATES)
        winner = results[0]
        cta_factors = [f for f in winner.factors if "cta" in f.name.lower() or "CTA" in f.detail]
        assert any(f.value > 0 for f in cta_factors)


class TestExamplePostReleaseSustain:
    """Example 3: Post-release sustain — maximize saves and engagement depth."""

    PATTERNS = [
        _winning("content_type", "carousel", confidence=0.85, lift=0.30),
        _winning("caption_length_bucket", "long", confidence=0.75, lift=0.20),
        _winning("hook_type", "statistic", confidence=0.70, lift=0.15),
        _losing("hashtag_count_bucket", "many", confidence=0.65, lift=-0.10),
        _losing("content_type", "story", confidence=0.72, lift=-0.18),
    ]

    CANDIDATES = [
        CandidatePost(
            candidate_id="deep_carousel",
            platform="instagram",
            content_type="carousel",
            post_hour=10,
            hook_type="statistic",
            cta_type="save",
            has_media=True,
            caption_length=280,
            hashtag_count=8,
        ),
        CandidatePost(
            candidate_id="quick_reel",
            platform="instagram",
            content_type="reel",
            post_hour=18,
            hook_type="teaser",
            cta_type="follow",
            has_media=True,
            caption_length=60,
            hashtag_count=12,
        ),
        CandidatePost(
            candidate_id="spammy_story",
            platform="instagram",
            content_type="story",
            post_hour=15,
            hook_type="quote",
            cta_type="none",
            has_media=True,
            caption_length=30,
            hashtag_count=25,
        ),
    ]

    def test_deep_carousel_wins_post_release(self):
        ranker = PostRanker(patterns=self.PATTERNS, profile="post_release")
        results = ranker.rank(self.CANDIDATES)
        assert results[0].candidate_id == "deep_carousel"

    def test_spammy_story_ranks_last(self):
        ranker = PostRanker(patterns=self.PATTERNS, profile="post_release")
        results = ranker.rank(self.CANDIDATES)
        assert results[-1].candidate_id == "spammy_story"

    def test_spammy_story_has_multiple_penalties(self):
        ranker = PostRanker(patterns=self.PATTERNS, profile="post_release")
        results = ranker.rank(self.CANDIDATES)
        spammy = [r for r in results if r.candidate_id == "spammy_story"][0]
        negative_factors = [f for f in spammy.factors if f.value < 0]
        # Should have: losing pattern (story), excessive hashtags, missing CTA, multiple losing
        assert len(negative_factors) >= 3

    def test_all_scores_decomposable(self):
        ranker = PostRanker(patterns=self.PATTERNS, profile="post_release")
        for r in ranker.rank(self.CANDIDATES):
            assert abs(r.score - sum(f.value for f in r.factors)) < 1e-6

    def test_profiles_produce_different_rankings(self):
        """Same candidates ranked differently across profiles."""
        pre = PostRanker(patterns=self.PATTERNS, profile="pre_release")
        post = PostRanker(patterns=self.PATTERNS, profile="post_release")
        pre_order = [r.candidate_id for r in pre.rank(self.CANDIDATES)]
        post_order = [r.candidate_id for r in post.rank(self.CANDIDATES)]
        # At minimum the scores should differ (order may or may not)
        pre_scores = {r.candidate_id: r.score for r in pre.rank(self.CANDIDATES)}
        post_scores = {r.candidate_id: r.score for r in post.rank(self.CANDIDATES)}
        assert pre_scores != post_scores

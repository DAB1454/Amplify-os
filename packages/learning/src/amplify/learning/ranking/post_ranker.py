"""Post ranker — rule-based ranking engine for candidate posts.

Scores and ranks candidate post variants using tenant patterns, reward
heuristics, and policy constraints.  This is the safe, pre-bandit
intelligence layer: every score is decomposable into named factors,
every ranking is deterministic (given the same inputs), and every
decision is explainable.

Ranking profiles:
    pre_release  — optimize for presaves, follower growth, email capture
    release_day  — optimize for clicks-to-streaming, shares, conversion
    post_release — optimize for sustained engagement and saves

Usage from planner_agent or publisher workflow::

    from amplify.learning.ranking.post_ranker import PostRanker, CandidatePost

    ranker = PostRanker(patterns=patterns, profile="pre_release")
    ranked = ranker.rank([candidate_a, candidate_b, candidate_c])
    # ranked[0] is the best candidate, with .explanation for transparency
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Candidate and result models ──────────────────────────────────


@dataclass(frozen=True)
class CandidatePost:
    """A candidate post variant to be ranked.

    All fields are optional except candidate_id — the ranker scores
    whatever features are present and notes missing ones.
    """

    candidate_id: str
    platform: str = ""
    content_type: str = ""            # post, reel, short, story, carousel
    post_hour: int | None = None      # 0-23
    post_day_of_week: int | None = None  # 0=Mon, 6=Sun
    caption_length: int | None = None
    hashtag_count: int | None = None
    has_link: bool | None = None
    has_media: bool | None = None
    media_count: int | None = None
    hook_type: str = ""               # question, statistic, teaser, quote
    cta_type: str = ""                # link, presave, follow, none
    campaign_phase: str = ""          # pre_release, release_day, post_release
    extra: dict[str, Any] = field(default_factory=dict)

    def to_features(self) -> dict[str, Any]:
        """Flatten into a feature dict for scoring."""
        features: dict[str, Any] = {}
        if self.platform:
            features["platform"] = self.platform
        if self.content_type:
            features["content_type"] = self.content_type
        if self.post_hour is not None:
            features["post_hour"] = self.post_hour
        if self.post_day_of_week is not None:
            features["post_day_of_week"] = self.post_day_of_week
        if self.caption_length is not None:
            features["caption_length_bucket"] = _caption_bucket(self.caption_length)
        if self.hashtag_count is not None:
            features["hashtag_count_bucket"] = _hashtag_bucket(self.hashtag_count)
        if self.has_link is not None:
            features["has_link"] = str(self.has_link)
        if self.has_media is not None:
            features["has_media"] = str(self.has_media)
        if self.hook_type:
            features["hook_type"] = self.hook_type
        if self.cta_type:
            features["cta_type"] = self.cta_type
        features.update(self.extra)
        return features


@dataclass
class ScoreFactor:
    """One named factor contributing to a candidate's total score."""

    name: str
    value: float           # contribution to the total
    weight: float          # the weight that was applied
    raw: float             # pre-weight score
    source: str            # "pattern", "heuristic", or "policy"
    detail: str            # human-readable explanation


@dataclass
class RankedPost:
    """A scored and ranked candidate post."""

    candidate_id: str
    rank: int
    score: float
    factors: list[ScoreFactor]
    profile: str               # which ranking profile was used
    warnings: list[str] = field(default_factory=list)

    @property
    def explanation(self) -> str:
        """Human-readable explanation of the ranking."""
        lines = [f"Rank #{self.rank} — score {self.score:.4f} (profile={self.profile})"]
        top = sorted(self.factors, key=lambda f: abs(f.value), reverse=True)
        for f in top[:5]:
            sign = "+" if f.value >= 0 else ""
            lines.append(f"  {sign}{f.value:.4f}  {f.detail}")
        if self.warnings:
            lines.append(f"  Warnings: {'; '.join(self.warnings)}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "rank": self.rank,
            "score": round(self.score, 6),
            "profile": self.profile,
            "factors": [
                {
                    "name": f.name,
                    "value": round(f.value, 6),
                    "weight": round(f.weight, 4),
                    "raw": round(f.raw, 6),
                    "source": f.source,
                    "detail": f.detail,
                }
                for f in self.factors
            ],
            "warnings": self.warnings,
        }


# ── Tenant pattern wrapper ───────────────────────────────────────


@dataclass(frozen=True)
class PatternSignal:
    """A learned pattern to inform ranking.

    Maps directly from TenantPatternModel but kept as a plain dataclass
    so the ranker has no ORM dependency.
    """

    pattern_type: str          # timing, content, platform, audience, campaign
    subject_feature: str       # e.g. "post_hour", "content_type"
    subject_value: str         # e.g. "18", "reel"
    direction: str             # "winning" or "losing"
    confidence: float          # 0-1
    lift_vs_baseline: float = 0.0
    decay_weighted_reward: float = 0.0
    is_pinned: bool = False


# ── Ranking profiles ─────────────────────────────────────────────

# Each profile defines feature weights and heuristic weights for a
# specific campaign phase.  Weights are relative — they're normalized
# at scoring time.

_PROFILE_FEATURE_WEIGHTS: dict[str, dict[str, float]] = {
    "pre_release": {
        "content_type": 1.2,
        "hook_type": 1.0,
        "cta_type": 1.5,       # presave CTA is critical pre-release
        "post_hour": 0.8,
        "post_day_of_week": 0.6,
        "platform": 0.7,
        "has_link": 1.0,
        "caption_length_bucket": 0.4,
        "hashtag_count_bucket": 0.3,
        "has_media": 0.5,
    },
    "release_day": {
        "content_type": 1.0,
        "hook_type": 0.8,
        "cta_type": 1.8,       # click-to-stream CTA is everything
        "post_hour": 1.2,      # timing is critical on release day
        "post_day_of_week": 0.3,
        "platform": 1.0,
        "has_link": 1.5,       # must have streaming link
        "caption_length_bucket": 0.3,
        "hashtag_count_bucket": 0.3,
        "has_media": 0.6,
    },
    "post_release": {
        "content_type": 1.3,   # content type drives sustained saves
        "hook_type": 1.0,
        "cta_type": 0.6,
        "post_hour": 0.7,
        "post_day_of_week": 0.5,
        "platform": 0.8,
        "has_link": 0.5,
        "caption_length_bucket": 0.6,
        "hashtag_count_bucket": 0.5,
        "has_media": 0.7,
    },
}

# Heuristic weights per profile (applied independently of patterns)
_PROFILE_HEURISTIC_WEIGHTS: dict[str, dict[str, float]] = {
    "pre_release": {
        "cta_alignment": 1.5,
        "content_format_bonus": 0.8,
        "timing_prime": 0.6,
    },
    "release_day": {
        "cta_alignment": 2.0,
        "content_format_bonus": 0.6,
        "timing_prime": 1.0,
    },
    "post_release": {
        "cta_alignment": 0.6,
        "content_format_bonus": 1.0,
        "timing_prime": 0.5,
    },
}

# Policy penalties — same across profiles (safety layer)
_POLICY_PENALTIES: dict[str, float] = {
    "missing_cta_penalty": -0.15,
    "losing_pattern_penalty": -0.20,
    "no_media_penalty": -0.10,
    "excessive_hashtags_penalty": -0.08,
}

# Default profile for phases not explicitly mapped
_PHASE_TO_PROFILE: dict[str, str] = {
    "early_announce": "pre_release",
    "pre_release": "pre_release",
    "countdown": "pre_release",
    "release_day": "release_day",
    "release_week": "release_day",
    "post_release": "post_release",
    "sustain": "post_release",
    "evergreen": "post_release",
}

# CTA types considered aligned with each profile
_CTA_ALIGNMENT: dict[str, set[str]] = {
    "pre_release": {"presave", "email", "follow"},
    "release_day": {"link", "stream", "buy"},
    "post_release": {"link", "follow", "save"},
}

# Content types that get a format bonus per profile
_FORMAT_BONUS: dict[str, set[str]] = {
    "pre_release": {"reel", "short", "story"},
    "release_day": {"reel", "short", "post", "carousel"},
    "post_release": {"carousel", "reel", "post"},
}

# Prime posting hours (high general engagement)
_PRIME_HOURS: set[int] = {9, 10, 11, 12, 17, 18, 19, 20}


# ── Bucket helpers ───────────────────────────────────────────────


def _caption_bucket(length: int) -> str:
    if length < 50:
        return "short"
    if length < 150:
        return "medium"
    if length < 300:
        return "long"
    return "very_long"


def _hashtag_bucket(count: int) -> str:
    if count == 0:
        return "none"
    if count <= 5:
        return "few"
    if count <= 15:
        return "moderate"
    return "many"


# ── The ranker ───────────────────────────────────────────────────


class PostRanker:
    """Rule-based ranking engine for candidate posts.

    Scores are composed of three layers:
    1. **Pattern scores** — learned tenant patterns matched to candidate features
    2. **Heuristic scores** — rule-based bonuses (CTA alignment, format, timing)
    3. **Policy adjustments** — penalties for constraint violations

    All three are decomposed into named ScoreFactors for transparency.

    Usage::

        patterns = [PatternSignal(...), ...]
        ranker = PostRanker(patterns=patterns, profile="release_day")
        results = ranker.rank([candidate_a, candidate_b])
        best = results[0]
        print(best.explanation)
    """

    def __init__(
        self,
        patterns: list[PatternSignal] | None = None,
        profile: str = "post_release",
        custom_weights: dict[str, float] | None = None,
    ) -> None:
        self._patterns = patterns or []
        self._profile = _PHASE_TO_PROFILE.get(profile, profile)
        if self._profile not in _PROFILE_FEATURE_WEIGHTS:
            self._profile = "post_release"
        self._feature_weights = dict(_PROFILE_FEATURE_WEIGHTS[self._profile])
        self._heuristic_weights = dict(_PROFILE_HEURISTIC_WEIGHTS[self._profile])
        if custom_weights:
            self._feature_weights.update(custom_weights)

        # Index patterns for fast lookup
        self._winning: dict[str, dict[str, PatternSignal]] = {}
        self._losing: dict[str, dict[str, PatternSignal]] = {}
        for p in self._patterns:
            bucket = self._winning if p.direction == "winning" else self._losing
            bucket.setdefault(p.subject_feature, {})[p.subject_value] = p

    def rank(self, candidates: list[CandidatePost]) -> list[RankedPost]:
        """Score and rank candidates. Returns list sorted by score descending."""
        if not candidates:
            return []

        results: list[RankedPost] = []
        for c in candidates:
            factors, warnings = self._score(c)
            total = sum(f.value for f in factors)
            results.append(RankedPost(
                candidate_id=c.candidate_id,
                rank=0,
                score=total,
                factors=factors,
                profile=self._profile,
                warnings=warnings,
            ))

        # Deterministic sort: score desc, then candidate_id asc for ties
        results.sort(key=lambda r: (-r.score, r.candidate_id))

        for i, r in enumerate(results, 1):
            r.rank = i

        return results

    # ── Scoring ──────────────────────────────────────────────────

    def _score(self, candidate: CandidatePost) -> tuple[list[ScoreFactor], list[str]]:
        """Compute all score factors for a candidate."""
        factors: list[ScoreFactor] = []
        warnings: list[str] = []

        features = candidate.to_features()

        # Layer 1: Pattern matching
        matched_any = False
        for feat_name, feat_value in features.items():
            str_val = str(feat_value)
            weight = self._feature_weights.get(feat_name, 0.5)

            # Check for winning pattern
            winning = self._winning.get(feat_name, {}).get(str_val)
            if winning:
                matched_any = True
                raw = winning.confidence * max(winning.lift_vs_baseline, 0.1)
                factors.append(ScoreFactor(
                    name=f"pattern_{feat_name}_win",
                    value=round(raw * weight, 6),
                    weight=weight,
                    raw=round(raw, 6),
                    source="pattern",
                    detail=(
                        f"Winning pattern: {feat_name}={str_val} "
                        f"(confidence={winning.confidence:.2f}, "
                        f"lift={winning.lift_vs_baseline:+.2f})"
                    ),
                ))

            # Check for losing pattern
            losing = self._losing.get(feat_name, {}).get(str_val)
            if losing:
                matched_any = True
                raw = -(losing.confidence * max(abs(losing.lift_vs_baseline), 0.1))
                factors.append(ScoreFactor(
                    name=f"pattern_{feat_name}_lose",
                    value=round(raw * weight, 6),
                    weight=weight,
                    raw=round(raw, 6),
                    source="pattern",
                    detail=(
                        f"Losing pattern: {feat_name}={str_val} "
                        f"(confidence={losing.confidence:.2f}, "
                        f"lift={losing.lift_vs_baseline:+.2f})"
                    ),
                ))

        if not matched_any and self._patterns:
            warnings.append("No patterns matched any candidate features")

        # Layer 2: Heuristics
        factors.extend(self._heuristic_factors(candidate))

        # Layer 3: Policy constraints
        factors.extend(self._policy_factors(candidate, warnings))

        return factors, warnings

    def _heuristic_factors(self, c: CandidatePost) -> list[ScoreFactor]:
        """Rule-based heuristic bonuses."""
        factors: list[ScoreFactor] = []
        hw = self._heuristic_weights

        # CTA alignment with campaign profile
        aligned_ctas = _CTA_ALIGNMENT.get(self._profile, set())
        cta_w = hw.get("cta_alignment", 1.0)
        if c.cta_type and c.cta_type in aligned_ctas:
            factors.append(ScoreFactor(
                name="cta_alignment",
                value=round(0.15 * cta_w, 6),
                weight=cta_w,
                raw=0.15,
                source="heuristic",
                detail=f"CTA '{c.cta_type}' aligns with {self._profile} goals",
            ))
        elif c.cta_type and c.cta_type not in aligned_ctas:
            factors.append(ScoreFactor(
                name="cta_misalignment",
                value=round(-0.05 * cta_w, 6),
                weight=cta_w,
                raw=-0.05,
                source="heuristic",
                detail=f"CTA '{c.cta_type}' not ideal for {self._profile} profile",
            ))

        # Content format bonus
        bonus_formats = _FORMAT_BONUS.get(self._profile, set())
        fmt_w = hw.get("content_format_bonus", 1.0)
        if c.content_type and c.content_type in bonus_formats:
            factors.append(ScoreFactor(
                name="content_format_bonus",
                value=round(0.10 * fmt_w, 6),
                weight=fmt_w,
                raw=0.10,
                source="heuristic",
                detail=f"Content type '{c.content_type}' is strong for {self._profile}",
            ))

        # Prime timing bonus
        timing_w = hw.get("timing_prime", 1.0)
        if c.post_hour is not None and c.post_hour in _PRIME_HOURS:
            factors.append(ScoreFactor(
                name="timing_prime_hours",
                value=round(0.08 * timing_w, 6),
                weight=timing_w,
                raw=0.08,
                source="heuristic",
                detail=f"Hour {c.post_hour}:00 is in the prime engagement window",
            ))

        return factors

    def _policy_factors(
        self, c: CandidatePost, warnings: list[str],
    ) -> list[ScoreFactor]:
        """Policy constraint penalties."""
        factors: list[ScoreFactor] = []

        # Missing CTA penalty
        if not c.cta_type or c.cta_type == "none":
            penalty = _POLICY_PENALTIES["missing_cta_penalty"]
            factors.append(ScoreFactor(
                name="missing_cta",
                value=penalty,
                weight=1.0,
                raw=penalty,
                source="policy",
                detail="No CTA specified — reduces discoverability",
            ))
            warnings.append("Candidate has no CTA")

        # Losing pattern penalty (on top of pattern score — extra emphasis)
        features = c.to_features()
        losing_count = 0
        for feat_name, feat_value in features.items():
            if str(feat_value) in self._losing.get(feat_name, {}):
                losing_count += 1
        if losing_count >= 2:
            penalty = _POLICY_PENALTIES["losing_pattern_penalty"]
            factors.append(ScoreFactor(
                name="multiple_losing_patterns",
                value=penalty,
                weight=1.0,
                raw=penalty,
                source="policy",
                detail=f"Matches {losing_count} losing patterns — high risk of underperformance",
            ))
            warnings.append(f"Candidate matches {losing_count} losing patterns")

        # No media penalty (platform content without visuals)
        if c.has_media is False and c.platform in ("instagram", "tiktok"):
            penalty = _POLICY_PENALTIES["no_media_penalty"]
            factors.append(ScoreFactor(
                name="no_media",
                value=penalty,
                weight=1.0,
                raw=penalty,
                source="policy",
                detail=f"No media on {c.platform} — visual platforms penalize text-only",
            ))

        # Excessive hashtags penalty
        if c.hashtag_count is not None and c.hashtag_count > 20:
            penalty = _POLICY_PENALTIES["excessive_hashtags_penalty"]
            factors.append(ScoreFactor(
                name="excessive_hashtags",
                value=penalty,
                weight=1.0,
                raw=penalty,
                source="policy",
                detail=f"{c.hashtag_count} hashtags — may trigger spam filters",
            ))
            warnings.append("Excessive hashtag count")

        return factors

    # ── Convenience constructors ─────────────────────────────────

    @classmethod
    def from_pattern_dicts(
        cls,
        pattern_dicts: list[dict[str, Any]],
        profile: str = "post_release",
    ) -> PostRanker:
        """Create a ranker from raw pattern dicts (e.g., from DB query).

        Expects dicts with keys matching PatternSignal fields,
        or TenantPatternModel serializations with an 'evidence' sub-dict.
        """
        signals: list[PatternSignal] = []
        for d in pattern_dicts:
            evidence = d.get("evidence", {})
            signals.append(PatternSignal(
                pattern_type=d.get("pattern_type", ""),
                subject_feature=d.get("subject_feature", ""),
                subject_value=d.get("subject_value", ""),
                direction=d.get("direction", "winning"),
                confidence=d.get("confidence", 0.5),
                lift_vs_baseline=evidence.get("lift_vs_baseline", d.get("lift_vs_baseline", 0.0)),
                decay_weighted_reward=d.get("decay_weighted_reward", 0.0),
                is_pinned=d.get("is_pinned", False),
            ))
        return cls(patterns=signals, profile=profile)

    @classmethod
    def for_campaign_phase(
        cls,
        phase: str,
        patterns: list[PatternSignal] | None = None,
    ) -> PostRanker:
        """Create a ranker pre-configured for a campaign phase."""
        return cls(patterns=patterns, profile=phase)

"""Offline replay evaluation engine.

Replays historical post opportunities through ranking policies and prompt
versions, comparing predicted scores against actual observed rewards.
All evaluation is deterministic and reproducible — the same dataset +
policy always produces the same results.

Usage::

    dataset = ReplayDataset(opportunities=[...])
    policy_a = RankingPolicySpec(name="current", ranker=PostRanker(...))
    policy_b = RankingPolicySpec(name="candidate", ranker=PostRanker(...))

    engine = ReplayEngine()
    report = engine.evaluate(dataset, [policy_a, policy_b])
    print(report.summary())
"""

from __future__ import annotations

import math
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from amplify.learning.ranking.post_ranker import (
    CandidatePost,
    PatternSignal,
    PostRanker,
    RankedPost,
)


# ── Dataset ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class PostOpportunity:
    """A historical post that was published — features + measured outcome.

    This is one row in the replay dataset.  Features are what was known
    at publish time; reward is the actual measured outcome.
    """

    post_id: str
    platform: str = ""
    content_type: str = ""
    post_hour: int | None = None
    post_day_of_week: int | None = None
    caption_length: int | None = None
    hashtag_count: int | None = None
    has_link: bool | None = None
    has_media: bool | None = None
    hook_type: str = ""
    cta_type: str = ""
    campaign_phase: str = ""

    # Actual observed outcome
    actual_reward: float = 0.0
    actual_engagement_rate: float | None = None
    actual_save_rate: float | None = None
    actual_click_through_rate: float | None = None

    # Metadata for grouping
    tenant_id: str = ""
    campaign_id: str = ""
    published_at: str = ""       # ISO datetime
    prompt_version_id: str = ""  # which prompt version produced the content

    extra: dict[str, Any] = field(default_factory=dict)

    def to_candidate(self) -> CandidatePost:
        """Convert to a CandidatePost for scoring."""
        return CandidatePost(
            candidate_id=self.post_id,
            platform=self.platform,
            content_type=self.content_type,
            post_hour=self.post_hour,
            post_day_of_week=self.post_day_of_week,
            caption_length=self.caption_length,
            hashtag_count=self.hashtag_count,
            has_link=self.has_link,
            has_media=self.has_media,
            hook_type=self.hook_type,
            cta_type=self.cta_type,
            campaign_phase=self.campaign_phase,
            extra=self.extra,
        )


@dataclass
class ReplayDataset:
    """A collection of historical post opportunities for evaluation."""

    opportunities: list[PostOpportunity] = field(default_factory=list)
    name: str = ""
    description: str = ""
    window_start: str = ""  # ISO datetime
    window_end: str = ""

    @property
    def size(self) -> int:
        return len(self.opportunities)

    @property
    def platforms(self) -> set[str]:
        return {o.platform for o in self.opportunities if o.platform}

    @property
    def mean_actual_reward(self) -> float:
        if not self.opportunities:
            return 0.0
        return statistics.mean(o.actual_reward for o in self.opportunities)

    def filter_by_platform(self, platform: str) -> ReplayDataset:
        return ReplayDataset(
            opportunities=[o for o in self.opportunities if o.platform == platform],
            name=f"{self.name}__{platform}",
            window_start=self.window_start,
            window_end=self.window_end,
        )

    def filter_by_prompt_version(self, version_id: str) -> ReplayDataset:
        return ReplayDataset(
            opportunities=[o for o in self.opportunities if o.prompt_version_id == version_id],
            name=f"{self.name}__pv_{version_id[:8]}",
            window_start=self.window_start,
            window_end=self.window_end,
        )


# ── Policy spec ──────────────────────────────────────────────────


class RankingPolicy(Protocol):
    """Protocol for anything that can score a list of candidates."""

    def rank(self, candidates: list[CandidatePost]) -> list[RankedPost]: ...


@dataclass
class RankingPolicySpec:
    """A ranking policy to evaluate."""

    name: str
    ranker: RankingPolicy
    description: str = ""
    prompt_version_id: str = ""   # associated prompt version, if any


# ── Per-post result ──────────────────────────────────────────────


@dataclass
class PostEvalResult:
    """Evaluation result for a single post opportunity."""

    post_id: str
    predicted_score: float
    predicted_rank: int
    actual_reward: float
    actual_rank: int
    absolute_error: float
    platform: str = ""
    factors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def rank_error(self) -> int:
        return abs(self.predicted_rank - self.actual_rank)


# ── Policy evaluation report ─────────────────────────────────────


@dataclass
class PolicyEvalReport:
    """Evaluation report for a single ranking policy."""

    policy_name: str
    sample_size: int = 0

    # Core metrics
    mae: float | None = None                  # mean absolute error
    rank_correlation: float | None = None     # Spearman
    top_k_precision: float | None = None      # fraction of top-k correct
    top_k: int = 3

    # Expected reward if this policy had been used to select the top pick
    expected_reward_top1: float | None = None
    expected_reward_top3: float | None = None

    # Risk: how often does the policy rank a bad post first?
    risk_score: float | None = None  # fraction of top picks below median reward

    # Confidence interval on expected reward (bootstrap-free normal approx)
    expected_reward_ci_lower: float | None = None
    expected_reward_ci_upper: float | None = None

    # Platform-specific lift
    platform_lift: dict[str, float] = field(default_factory=dict)

    # Per-post detail
    results: list[PostEvalResult] = field(default_factory=list)

    # Metadata
    policy_description: str = ""
    prompt_version_id: str = ""
    evaluated_at: str = ""

    @property
    def is_useful(self) -> bool:
        if self.rank_correlation is not None and self.rank_correlation > 0.3:
            return True
        if self.top_k_precision is not None and self.top_k_precision > 0.33:
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_name": self.policy_name,
            "sample_size": self.sample_size,
            "mae": _r(self.mae),
            "rank_correlation": _r(self.rank_correlation),
            "top_k_precision": _r(self.top_k_precision),
            "top_k": self.top_k,
            "expected_reward_top1": _r(self.expected_reward_top1),
            "expected_reward_top3": _r(self.expected_reward_top3),
            "risk_score": _r(self.risk_score),
            "expected_reward_ci_lower": _r(self.expected_reward_ci_lower),
            "expected_reward_ci_upper": _r(self.expected_reward_ci_upper),
            "platform_lift": {k: _r(v) for k, v in self.platform_lift.items()},
            "is_useful": self.is_useful,
            "prompt_version_id": self.prompt_version_id,
        }


@dataclass
class ComparisonReport:
    """Side-by-side comparison of multiple policies on the same dataset."""

    dataset_name: str
    dataset_size: int
    policies: list[PolicyEvalReport]
    winner: str = ""           # name of the best policy
    winner_reason: str = ""
    evaluated_at: str = ""

    def summary(self) -> str:
        lines = [
            f"Evaluation: {self.dataset_name} ({self.dataset_size} posts)",
            f"Evaluated at: {self.evaluated_at}",
            "",
        ]
        for p in self.policies:
            lines.append(f"── {p.policy_name} ──")
            lines.append(f"  MAE:              {_fmt(p.mae)}")
            lines.append(f"  Rank correlation: {_fmt(p.rank_correlation)}")
            lines.append(f"  Top-{p.top_k} precision: {_fmt(p.top_k_precision)}")
            lines.append(f"  Expected reward (top-1): {_fmt(p.expected_reward_top1)}")
            lines.append(f"  Expected reward (top-3): {_fmt(p.expected_reward_top3)}")
            lines.append(f"  Risk score:       {_fmt(p.risk_score)}")
            if p.platform_lift:
                for plat, lift in p.platform_lift.items():
                    lines.append(f"  Lift ({plat}): {lift:+.4f}")
            lines.append(f"  Useful: {p.is_useful}")
            lines.append("")

        if self.winner:
            lines.append(f"Winner: {self.winner}")
            lines.append(f"Reason: {self.winner_reason}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_name": self.dataset_name,
            "dataset_size": self.dataset_size,
            "policies": [p.to_dict() for p in self.policies],
            "winner": self.winner,
            "winner_reason": self.winner_reason,
            "evaluated_at": self.evaluated_at,
        }


# ── Replay engine ────────────────────────────────────────────────


class ReplayEngine:
    """Replays historical opportunities through ranking policies.

    Deterministic: same dataset + same policies → same results.
    """

    def evaluate(
        self,
        dataset: ReplayDataset,
        policies: list[RankingPolicySpec],
        top_k: int = 3,
    ) -> ComparisonReport:
        """Run all policies against the dataset and compare."""
        if not dataset.opportunities:
            return ComparisonReport(
                dataset_name=dataset.name,
                dataset_size=0,
                policies=[],
                evaluated_at=datetime.now(timezone.utc).isoformat(),
            )

        policy_reports: list[PolicyEvalReport] = []
        for spec in policies:
            report = self._evaluate_policy(dataset, spec, top_k)
            policy_reports.append(report)

        winner, reason = self._pick_winner(policy_reports)

        return ComparisonReport(
            dataset_name=dataset.name,
            dataset_size=dataset.size,
            policies=policy_reports,
            winner=winner,
            winner_reason=reason,
            evaluated_at=datetime.now(timezone.utc).isoformat(),
        )

    def evaluate_single(
        self,
        dataset: ReplayDataset,
        policy: RankingPolicySpec,
        top_k: int = 3,
    ) -> PolicyEvalReport:
        """Evaluate a single policy against the dataset."""
        return self._evaluate_policy(dataset, policy, top_k)

    # ── Internal ──────────────────────────────────────────────────

    def _evaluate_policy(
        self,
        dataset: ReplayDataset,
        spec: RankingPolicySpec,
        top_k: int,
    ) -> PolicyEvalReport:
        """Score all opportunities with the policy and compare to actuals."""
        candidates = [o.to_candidate() for o in dataset.opportunities]
        ranked = spec.ranker.rank(candidates)

        # Build lookup: candidate_id → (predicted_score, predicted_rank, factors)
        pred_lookup: dict[str, tuple[float, int, list[dict]]] = {}
        for r in ranked:
            factors = [
                {"name": f.name, "value": f.value, "source": f.source, "detail": f.detail}
                for f in r.factors
            ]
            pred_lookup[r.candidate_id] = (r.score, r.rank, factors)

        # Build actual rankings (by reward descending)
        sorted_by_reward = sorted(
            dataset.opportunities,
            key=lambda o: o.actual_reward,
            reverse=True,
        )
        actual_rank_map: dict[str, int] = {}
        for i, o in enumerate(sorted_by_reward, 1):
            actual_rank_map[o.post_id] = i

        # Per-post results
        results: list[PostEvalResult] = []
        for opp in dataset.opportunities:
            pred_score, pred_rank, factors = pred_lookup.get(
                opp.post_id, (0.0, len(dataset.opportunities), [])
            )
            actual_rank = actual_rank_map.get(opp.post_id, len(dataset.opportunities))
            results.append(PostEvalResult(
                post_id=opp.post_id,
                predicted_score=pred_score,
                predicted_rank=pred_rank,
                actual_reward=opp.actual_reward,
                actual_rank=actual_rank,
                absolute_error=abs(pred_score - opp.actual_reward),
                platform=opp.platform,
                factors=factors,
            ))

        n = len(results)
        pred_scores = [r.predicted_score for r in results]
        actual_rewards = [r.actual_reward for r in results]

        # MAE
        mae = statistics.mean(r.absolute_error for r in results)

        # Rank correlation
        rank_corr = _spearman(pred_scores, actual_rewards) if n >= 3 else None

        # Top-k precision
        k = min(top_k, n)
        pred_top_k = {r.post_id for r in sorted(results, key=lambda r: r.predicted_score, reverse=True)[:k]}
        actual_top_k = {r.post_id for r in sorted(results, key=lambda r: r.actual_reward, reverse=True)[:k]}
        top_k_prec = len(pred_top_k & actual_top_k) / k if k > 0 else None

        # Expected reward: what reward would we get by following this policy?
        by_pred = sorted(results, key=lambda r: r.predicted_score, reverse=True)
        expected_top1 = by_pred[0].actual_reward if by_pred else None
        expected_top3 = (
            statistics.mean(r.actual_reward for r in by_pred[:k]) if k > 0 else None
        )

        # Risk: fraction of top picks below median reward
        if n >= 2:
            median_reward = statistics.median(actual_rewards)
            top_picks = by_pred[:k]
            risk = sum(1 for r in top_picks if r.actual_reward < median_reward) / k
        else:
            risk = None

        # Confidence interval on expected reward (normal approximation)
        ci_lower, ci_upper = None, None
        if n >= 5 and expected_top3 is not None:
            top_rewards = [r.actual_reward for r in by_pred[:k]]
            if len(top_rewards) >= 2:
                std = statistics.stdev(top_rewards)
                margin = 1.96 * std / math.sqrt(len(top_rewards))
                ci_lower = expected_top3 - margin
                ci_upper = expected_top3 + margin

        # Platform-specific lift
        baseline = statistics.mean(actual_rewards) if actual_rewards else 0
        platform_lift: dict[str, float] = {}
        for platform in dataset.platforms:
            plat_results = [r for r in by_pred[:k] if r.platform == platform]
            if plat_results:
                plat_mean = statistics.mean(r.actual_reward for r in plat_results)
                platform_lift[platform] = plat_mean - baseline

        return PolicyEvalReport(
            policy_name=spec.name,
            sample_size=n,
            mae=mae,
            rank_correlation=rank_corr,
            top_k_precision=top_k_prec,
            top_k=k,
            expected_reward_top1=expected_top1,
            expected_reward_top3=expected_top3,
            risk_score=risk,
            expected_reward_ci_lower=ci_lower,
            expected_reward_ci_upper=ci_upper,
            platform_lift=platform_lift,
            results=results,
            policy_description=spec.description,
            prompt_version_id=spec.prompt_version_id,
            evaluated_at=datetime.now(timezone.utc).isoformat(),
        )

    @staticmethod
    def _pick_winner(reports: list[PolicyEvalReport]) -> tuple[str, str]:
        """Pick the best policy based on expected reward, with rank correlation as tiebreak."""
        if not reports:
            return "", ""

        best = max(
            reports,
            key=lambda r: (r.expected_reward_top1 or 0, r.rank_correlation or 0),
        )

        reasons = []
        if best.expected_reward_top1 is not None:
            reasons.append(f"highest expected top-1 reward ({best.expected_reward_top1:.4f})")
        if best.rank_correlation is not None:
            reasons.append(f"rank correlation {best.rank_correlation:.4f}")

        return best.policy_name, "; ".join(reasons) if reasons else "default"


# ── Sample dataset builder ───────────────────────────────────────


def build_sample_dataset() -> ReplayDataset:
    """Build a sample replay dataset for testing and documentation.

    Contains 12 posts across Instagram/TikTok spanning pre-release
    through post-release phases, with realistic reward distributions.
    """
    opportunities = [
        # Pre-release phase — Instagram
        PostOpportunity(
            post_id="post-01", platform="instagram", content_type="reel",
            post_hour=18, post_day_of_week=2, caption_length=120,
            hashtag_count=8, has_link=False, has_media=True,
            hook_type="question", cta_type="presave",
            campaign_phase="pre_release",
            actual_reward=0.72, actual_engagement_rate=0.065,
        ),
        PostOpportunity(
            post_id="post-02", platform="instagram", content_type="carousel",
            post_hour=12, post_day_of_week=4, caption_length=250,
            hashtag_count=5, has_link=False, has_media=True,
            hook_type="teaser", cta_type="presave",
            campaign_phase="pre_release",
            actual_reward=0.55, actual_engagement_rate=0.042,
        ),
        PostOpportunity(
            post_id="post-03", platform="instagram", content_type="story",
            post_hour=22, post_day_of_week=5, caption_length=40,
            hashtag_count=2, has_link=False, has_media=True,
            hook_type="teaser", cta_type="follow",
            campaign_phase="pre_release",
            actual_reward=0.28, actual_engagement_rate=0.018,
        ),
        # Pre-release phase — TikTok
        PostOpportunity(
            post_id="post-04", platform="tiktok", content_type="short",
            post_hour=19, post_day_of_week=3, caption_length=60,
            hashtag_count=4, has_link=False, has_media=True,
            hook_type="question", cta_type="follow",
            campaign_phase="pre_release",
            actual_reward=0.61, actual_engagement_rate=0.058,
        ),
        # Release day — Instagram
        PostOpportunity(
            post_id="post-05", platform="instagram", content_type="reel",
            post_hour=10, post_day_of_week=4, caption_length=100,
            hashtag_count=10, has_link=True, has_media=True,
            hook_type="statistic", cta_type="link",
            campaign_phase="release_day",
            actual_reward=0.81, actual_engagement_rate=0.078,
            actual_click_through_rate=0.025,
        ),
        PostOpportunity(
            post_id="post-06", platform="instagram", content_type="post",
            post_hour=14, post_day_of_week=4, caption_length=300,
            hashtag_count=15, has_link=True, has_media=True,
            hook_type="quote", cta_type="link",
            campaign_phase="release_day",
            actual_reward=0.48, actual_engagement_rate=0.031,
        ),
        PostOpportunity(
            post_id="post-07", platform="instagram", content_type="story",
            post_hour=20, post_day_of_week=4, caption_length=30,
            hashtag_count=0, has_link=True, has_media=True,
            hook_type="teaser", cta_type="link",
            campaign_phase="release_day",
            actual_reward=0.35, actual_engagement_rate=0.022,
        ),
        # Release day — TikTok
        PostOpportunity(
            post_id="post-08", platform="tiktok", content_type="short",
            post_hour=12, post_day_of_week=4, caption_length=50,
            hashtag_count=3, has_link=True, has_media=True,
            hook_type="question", cta_type="link",
            campaign_phase="release_day",
            actual_reward=0.69, actual_engagement_rate=0.062,
        ),
        # Post-release — Instagram
        PostOpportunity(
            post_id="post-09", platform="instagram", content_type="carousel",
            post_hour=11, post_day_of_week=1, caption_length=280,
            hashtag_count=7, has_link=True, has_media=True,
            hook_type="statistic", cta_type="save",
            campaign_phase="post_release",
            actual_reward=0.65, actual_save_rate=0.038,
        ),
        PostOpportunity(
            post_id="post-10", platform="instagram", content_type="reel",
            post_hour=18, post_day_of_week=0, caption_length=90,
            hashtag_count=6, has_link=False, has_media=True,
            hook_type="question", cta_type="follow",
            campaign_phase="post_release",
            actual_reward=0.58, actual_save_rate=0.028,
        ),
        PostOpportunity(
            post_id="post-11", platform="instagram", content_type="story",
            post_hour=3, post_day_of_week=6, caption_length=20,
            hashtag_count=0, has_link=False, has_media=True,
            hook_type="teaser", cta_type="none",
            campaign_phase="post_release",
            actual_reward=0.15, actual_save_rate=0.005,
        ),
        # Post-release — TikTok
        PostOpportunity(
            post_id="post-12", platform="tiktok", content_type="short",
            post_hour=17, post_day_of_week=2, caption_length=70,
            hashtag_count=5, has_link=False, has_media=True,
            hook_type="question", cta_type="follow",
            campaign_phase="post_release",
            actual_reward=0.52, actual_engagement_rate=0.045,
        ),
    ]

    return ReplayDataset(
        opportunities=opportunities,
        name="sample_campaign_lifecycle",
        description="12-post sample spanning pre-release, release day, and post-release across Instagram and TikTok",
        window_start="2026-03-01T00:00:00Z",
        window_end="2026-03-15T00:00:00Z",
    )


# ── Helpers ──────────────────────────────────────────────────────


def _r(v: float | None) -> float | None:
    return round(v, 6) if v is not None else None


def _fmt(v: float | None) -> str:
    return f"{v:.4f}" if v is not None else "n/a"


def _spearman(x: list[float], y: list[float]) -> float:
    """Spearman rank correlation."""
    n = len(x)
    if n < 3:
        return 0.0

    def _ranks(values: list[float]) -> list[float]:
        sorted_indices = sorted(range(n), key=lambda i: values[i])
        ranks = [0.0] * n
        for rank, idx in enumerate(sorted_indices, 1):
            ranks[idx] = float(rank)
        return ranks

    rx = _ranks(x)
    ry = _ranks(y)
    d_sq = sum((a - b) ** 2 for a, b in zip(rx, ry))
    return 1 - (6 * d_sq) / (n * (n * n - 1))

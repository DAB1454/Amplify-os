"""Post scoring — normalized engagement and click performance.

Each post is scored on two axes:
  1. Engagement rate = (likes + comments + shares + saves) / impressions
  2. Click-through rate = clicks / impressions

Both are normalized into 0-100 percentile scores relative to the
cohort (all posts in the scoring window).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class PostScore:
    """Scoring result for a single post."""

    post_id: str
    platform: str = ""

    # Raw metrics
    impressions: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    clicks: int = 0

    # Computed rates (0.0-1.0)
    engagement_rate: float = 0.0
    click_through_rate: float = 0.0

    # Normalized percentile scores (0-100)
    engagement_score: float = 0.0
    click_score: float = 0.0
    composite_score: float = 0.0

    def compute_rates(self) -> None:
        """Compute engagement_rate and click_through_rate from raw metrics."""
        if self.impressions > 0:
            total_engagement = self.likes + self.comments + self.shares + self.saves
            self.engagement_rate = total_engagement / self.impressions
            self.click_through_rate = self.clicks / self.impressions
        else:
            self.engagement_rate = 0.0
            self.click_through_rate = 0.0


def score_post(
    post_id: str,
    platform: str = "",
    impressions: int = 0,
    likes: int = 0,
    comments: int = 0,
    shares: int = 0,
    saves: int = 0,
    clicks: int = 0,
) -> PostScore:
    """Create a PostScore with computed rates from raw metrics."""
    ps = PostScore(
        post_id=post_id,
        platform=platform,
        impressions=impressions,
        likes=likes,
        comments=comments,
        shares=shares,
        saves=saves,
        clicks=clicks,
    )
    ps.compute_rates()
    return ps


def _percentile_rank(value: float, sorted_values: list[float]) -> float:
    """Return 0-100 percentile rank of value within sorted_values."""
    n = len(sorted_values)
    if n == 0:
        return 50.0
    if n == 1:
        return 50.0
    count_below = sum(1 for v in sorted_values if v < value)
    count_equal = sum(1 for v in sorted_values if v == value)
    rank = (count_below + 0.5 * count_equal) / n * 100
    return round(rank, 1)


def normalize_scores(
    scores: list[PostScore],
    *,
    engagement_weight: float = 0.6,
    click_weight: float = 0.4,
) -> list[PostScore]:
    """Normalize engagement and click rates into percentile scores.

    Updates each PostScore in place with engagement_score, click_score,
    and composite_score (weighted average).
    """
    if not scores:
        return scores

    eng_rates = sorted(s.engagement_rate for s in scores)
    ctr_rates = sorted(s.click_through_rate for s in scores)

    for s in scores:
        s.engagement_score = _percentile_rank(s.engagement_rate, eng_rates)
        s.click_score = _percentile_rank(s.click_through_rate, ctr_rates)
        s.composite_score = round(
            s.engagement_score * engagement_weight + s.click_score * click_weight,
            1,
        )

    return scores


def score_posts(
    posts_data: list[dict],
    *,
    engagement_weight: float = 0.6,
    click_weight: float = 0.4,
) -> list[PostScore]:
    """Score a batch of posts from raw metric dicts.

    Each dict should have keys: post_id, platform, impressions, likes,
    comments, shares, saves, clicks.
    """
    scores = [
        score_post(
            post_id=d.get("post_id", ""),
            platform=d.get("platform", ""),
            impressions=d.get("impressions", 0),
            likes=d.get("likes", 0),
            comments=d.get("comments", 0),
            shares=d.get("shares", 0),
            saves=d.get("saves", 0),
            clicks=d.get("clicks", 0),
        )
        for d in posts_data
    ]
    return normalize_scores(
        scores,
        engagement_weight=engagement_weight,
        click_weight=click_weight,
    )

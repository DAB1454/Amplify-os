"""Weekly analyst — recommends keep/remix/stop for each post.

Scoring thresholds (configurable):
  - composite_score >= 70  → KEEP  (high performer, boost/repeat)
  - 40 <= composite_score < 70  → REMIX (moderate, tweak and retry)
  - composite_score < 40  → STOP  (underperforming, retire)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from amplify.core.analytics.scoring import PostScore, normalize_scores


class Verdict(str, Enum):
    KEEP = "keep"
    REMIX = "remix"
    STOP = "stop"


@dataclass
class PostVerdict:
    """Recommendation for a single post."""
    post_id: str
    platform: str
    composite_score: float
    engagement_score: float
    click_score: float
    verdict: Verdict
    reason: str


@dataclass
class AnalystRecommendation:
    """Full weekly report."""
    campaign_id: str
    period_start: str
    period_end: str
    total_posts: int
    verdicts: list[PostVerdict] = field(default_factory=list)
    summary: str = ""
    generated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def keep_count(self) -> int:
        return sum(1 for v in self.verdicts if v.verdict == Verdict.KEEP)

    @property
    def remix_count(self) -> int:
        return sum(1 for v in self.verdicts if v.verdict == Verdict.REMIX)

    @property
    def stop_count(self) -> int:
        return sum(1 for v in self.verdicts if v.verdict == Verdict.STOP)


def _classify(score: PostScore, keep_threshold: float, stop_threshold: float) -> PostVerdict:
    """Map a composite score to a keep/remix/stop verdict."""
    if score.composite_score >= keep_threshold:
        verdict = Verdict.KEEP
        reason = (
            f"High performer (score {score.composite_score:.0f}). "
            f"Engagement {score.engagement_score:.0f}p, CTR {score.click_score:.0f}p. "
            "Consider boosting or repeating this format."
        )
    elif score.composite_score >= stop_threshold:
        verdict = Verdict.REMIX
        reason = (
            f"Moderate performer (score {score.composite_score:.0f}). "
            f"Engagement {score.engagement_score:.0f}p, CTR {score.click_score:.0f}p. "
            "Try changing the hook, visual, or posting time."
        )
    else:
        verdict = Verdict.STOP
        reason = (
            f"Underperformer (score {score.composite_score:.0f}). "
            f"Engagement {score.engagement_score:.0f}p, CTR {score.click_score:.0f}p. "
            "Retire this content or rethink the approach."
        )
    return PostVerdict(
        post_id=score.post_id,
        platform=score.platform,
        composite_score=score.composite_score,
        engagement_score=score.engagement_score,
        click_score=score.click_score,
        verdict=verdict,
        reason=reason,
    )


def weekly_analyst_report(
    campaign_id: str,
    period_start: str,
    period_end: str,
    scores: list[PostScore],
    *,
    keep_threshold: float = 70.0,
    stop_threshold: float = 40.0,
) -> AnalystRecommendation:
    """Generate keep/remix/stop recommendations for a campaign's posts.

    Expects scores that have already been normalized (via normalize_scores).
    """
    verdicts = [_classify(s, keep_threshold, stop_threshold) for s in scores]

    keep = sum(1 for v in verdicts if v.verdict == Verdict.KEEP)
    remix = sum(1 for v in verdicts if v.verdict == Verdict.REMIX)
    stop = sum(1 for v in verdicts if v.verdict == Verdict.STOP)

    summary = (
        f"Analyzed {len(scores)} posts for {period_start} to {period_end}. "
        f"Recommendations: {keep} keep, {remix} remix, {stop} stop."
    )

    return AnalystRecommendation(
        campaign_id=campaign_id,
        period_start=period_start,
        period_end=period_end,
        total_posts=len(scores),
        verdicts=verdicts,
        summary=summary,
    )

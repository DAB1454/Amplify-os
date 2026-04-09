"""Confidence gate for auto-approval.

The worker's ``automation_tick`` calls this before flipping pending
approvals to "approved". It builds a ``CandidatePost`` from the post's
stored features, runs the tenant's bandit (which falls back to the
deterministic ``PostRanker`` when data is sparse) and returns a score.
Callers compare the score against a tenant-configurable threshold
(defaults to ``AMPLIFY_AUTO_APPROVE_MIN_SCORE`` env var, or ``0.0``).

The score is intentionally unbounded — it's the sum of weighted
factors, so "above zero" means the positive signals outweigh the
negative ones. A brand-new tenant with no learning data will mostly
hit heuristics-only scoring, which is fine: the gate is there to
*block* actively-bad posts (negative factor sums), not to pick winners.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def default_min_score() -> float:
    """Threshold below which auto-approval is blocked."""
    raw = os.environ.get("AMPLIFY_AUTO_APPROVE_MIN_SCORE")
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid AMPLIFY_AUTO_APPROVE_MIN_SCORE=%r, using 0.0", raw)
        return 0.0


@dataclass
class PostScore:
    """Result of scoring a single post for the confidence gate."""

    post_id: uuid.UUID
    score: float
    profile: str
    top_factors: list[str]  # short human-readable summaries for audit


async def score_post(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    post,  # PostModel — typed loosely to avoid import cycle
) -> PostScore:
    """Score one post using the tenant's bandit / ranker.

    Kept tolerant: any exception degrades gracefully to a neutral score
    of ``0.0`` so a ranker bug can't brick the whole autonomy loop.
    """
    from amplify.core.services.bandit_service import load_bandit
    from amplify.learning.ranking.post_ranker import CandidatePost

    try:
        candidate = _candidate_from_post(post)
        bandit = await load_bandit(db, tenant_id)
        ranked = bandit.rank([candidate])
        if not ranked:
            return PostScore(post.id, 0.0, "unknown", [])
        r = ranked[0]
        top = sorted(r.factors, key=lambda f: abs(f.value), reverse=True)[:3]
        summaries = [f"{f.name}={f.value:+.2f}" for f in top]
        return PostScore(post.id, float(r.score), r.profile, summaries)
    except Exception as exc:
        logger.warning(
            "score_post failed for tenant=%s post=%s: %s",
            tenant_id, getattr(post, "id", None), exc,
        )
        return PostScore(
            post_id=getattr(post, "id", uuid.uuid4()),
            score=0.0,
            profile="error",
            top_factors=[f"error:{type(exc).__name__}"],
        )


def _candidate_from_post(post):
    """Build a CandidatePost from a PostModel row."""
    from amplify.learning.ranking.post_ranker import CandidatePost

    scheduled: datetime | None = getattr(post, "scheduled_at", None)
    content_text: str = getattr(post, "content_text", "") or ""
    media_urls = getattr(post, "media_urls", None) or []
    destination_url = getattr(post, "destination_url", None)

    # Infer content_type from action_type_label when available, else fall
    # back to a platform-neutral "post". The ranker doesn't require this
    # to be any specific enum — it just keys feature weights off the string.
    content_type = (getattr(post, "action_type_label", None) or "post").lower()

    hashtag_count = content_text.count("#") if content_text else 0

    # Hook/CTA type are coarse: the ranker keys off the presence of a
    # link (CTA) and we don't try to auto-detect hook style from copy.
    cta_type = "link" if destination_url else "none"

    return CandidatePost(
        candidate_id=str(getattr(post, "id", "unknown")),
        platform=getattr(post, "platform", "") or "",
        content_type=content_type,
        post_hour=scheduled.hour if scheduled else None,
        post_day_of_week=scheduled.weekday() if scheduled else None,
        caption_length=len(content_text) if content_text else None,
        hashtag_count=hashtag_count,
        has_link=bool(destination_url),
        has_media=bool(media_urls),
        media_count=len(media_urls) if media_urls else 0,
        cta_type=cta_type,
        campaign_phase=_infer_phase(post),
    )


def _infer_phase(post) -> str:
    """Return a campaign phase string for profile selection.

    Tries the eagerly-loaded campaign relationship; falls back to the
    post-release profile which is the most permissive.
    """
    campaign = getattr(post, "campaign", None)
    if campaign is not None:
        phase = getattr(campaign, "phase", None)
        if phase:
            return str(phase)
    return "post_release"

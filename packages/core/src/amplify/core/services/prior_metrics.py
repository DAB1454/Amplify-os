"""Prior-metrics builder for the planner agent.

The planner template has a `{prior_metrics}` slot that's been empty since
day one. This module fills it: pull recent published posts, score them
with the analytics scoring module, and roll the result up into a compact
JSON shape the LLM can actually act on.

Design choices:
- We deliberately keep the payload SMALL. The planner sees thousands of
  tokens of prompt already; dumping every post would blow the budget and
  bury the signal.
- We surface BOTH winners and losers. "Avoid X" is just as actionable as
  "do more of Y", and the LLM behaves much better with both poles.
- Format/hour rollups are per-platform because what works on TikTok at
  noon doesn't work on YouTube at 9am.
- Hooks are excerpted (first 80 chars of caption) because the LLM
  pattern-matches on hook style — full captions are noise.
- If a tenant has fewer than 3 scored posts in window, we return an empty
  dict so the planner falls back to its phase-aware defaults instead of
  over-fitting to a tiny sample.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.core.analytics.scoring import score_posts
from amplify.db.models.post import PostModel


# How many top + bottom posts to surface to the planner. 5 is enough to
# show a pattern without burning tokens.
TOP_N = 5
BOTTOM_N = 5

# Below this many scored posts in the window we don't bother — the
# stats are too noisy to be worth feeding to the LLM.
MIN_SCORED_POSTS = 3


def _hook_excerpt(text: str | None, max_len: int = 80) -> str:
    """First-line excerpt of a caption, used as a 'hook' fingerprint."""
    if not text:
        return ""
    first_line = text.strip().split("\n", 1)[0].strip()
    if len(first_line) <= max_len:
        return first_line
    return first_line[: max_len - 1] + "…"


def _summarize_post(p: PostModel, score) -> dict[str, Any]:
    """Compact dict for one post — what the planner needs, nothing more."""
    sched = p.published_at or p.scheduled_at
    hour = sched.hour if sched else None
    dow = sched.strftime("%a") if sched else None
    return {
        "platform": p.platform or "",
        "format": p.action_type_label or "",
        "goal": getattr(p, "goal", None) or "",
        "score": round(score.composite_score, 1),
        "engagement_rate": round(score.engagement_rate, 4),
        "hook": _hook_excerpt(p.content_text),
        "hour": hour,
        "day_of_week": dow,
    }


async def build_prior_metrics_for_planner(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    lookback_days: int = 60,
) -> dict[str, Any]:
    """Roll up recent published posts into a planner-friendly metrics dict.

    Returns an empty dict when there are too few scored posts to be useful
    — the planner will then fall back to its phase-aware defaults.
    """
    cutoff = date.today() - timedelta(days=lookback_days)

    stmt = (
        select(PostModel)
        .where(
            PostModel.tenant_id == tenant_id,
            PostModel.status == "published",
            PostModel.published_at >= cutoff,
        )
        .order_by(PostModel.published_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()

    # Build the scoring inputs from posts that actually have engagement
    posts_with_engagement: list[tuple[PostModel, dict]] = []
    scoring_input: list[dict] = []
    for p in rows:
        eng = p.engagement or {}
        if not isinstance(eng, dict):
            continue
        # Skip posts where every metric is zero — they tell the scorer nothing
        if not any((eng.get(k) or 0) > 0 for k in (
            "impressions", "likes", "comments", "shares", "saves", "clicks", "views"
        )):
            continue
        # Some platforms report `views` instead of `impressions`. Treat
        # them as the same surface for scoring purposes.
        impressions = int(eng.get("impressions") or eng.get("views") or 0)
        scoring_input.append({
            "post_id": str(p.id),
            "platform": p.platform or "",
            "impressions": impressions,
            "likes": int(eng.get("likes") or 0),
            "comments": int(eng.get("comments") or 0),
            "shares": int(eng.get("shares") or 0),
            "saves": int(eng.get("saves") or 0),
            "clicks": int(eng.get("clicks") or 0),
        })
        posts_with_engagement.append((p, eng))

    if len(scoring_input) < MIN_SCORED_POSTS:
        return {}

    scores = score_posts(scoring_input)
    score_by_id = {s.post_id: s for s in scores}

    # Pair posts back to their score
    paired: list[tuple[PostModel, Any]] = []
    for p, _ in posts_with_engagement:
        s = score_by_id.get(str(p.id))
        if s is not None:
            paired.append((p, s))

    paired.sort(key=lambda ps: ps[1].composite_score, reverse=True)
    top = [_summarize_post(p, s) for p, s in paired[:TOP_N]]
    bottom = [_summarize_post(p, s) for p, s in paired[-BOTTOM_N:]][::-1]

    # Per-platform avg scores
    platform_scores: dict[str, list[float]] = defaultdict(list)
    for p, s in paired:
        platform_scores[(p.platform or "").lower()].append(s.composite_score)
    platform_avg = {
        plat: round(sum(vs) / len(vs), 1)
        for plat, vs in platform_scores.items()
        if vs
    }

    # Per-(platform, format) avg scores → pick best format per platform
    fmt_scores: dict[tuple[str, str], list[float]] = defaultdict(list)
    for p, s in paired:
        plat = (p.platform or "").lower()
        fmt = (p.action_type_label or "").lower()
        if plat and fmt:
            fmt_scores[(plat, fmt)].append(s.composite_score)
    best_format_per_platform: dict[str, dict[str, Any]] = {}
    for (plat, fmt), vs in fmt_scores.items():
        if len(vs) < 2:  # need at least 2 samples to call it a pattern
            continue
        avg = sum(vs) / len(vs)
        existing = best_format_per_platform.get(plat)
        if existing is None or avg > existing["avg_score"]:
            best_format_per_platform[plat] = {
                "format": fmt,
                "avg_score": round(avg, 1),
                "n": len(vs),
            }

    # Per-(platform, hour) avg scores → pick best hour per platform
    hour_scores: dict[tuple[str, int], list[float]] = defaultdict(list)
    for p, s in paired:
        sched = p.published_at or p.scheduled_at
        if not sched:
            continue
        plat = (p.platform or "").lower()
        if plat:
            hour_scores[(plat, sched.hour)].append(s.composite_score)
    best_hour_per_platform: dict[str, dict[str, Any]] = {}
    for (plat, hour), vs in hour_scores.items():
        if len(vs) < 2:
            continue
        avg = sum(vs) / len(vs)
        existing = best_hour_per_platform.get(plat)
        if existing is None or avg > existing["avg_score"]:
            best_hour_per_platform[plat] = {
                "hour": hour,
                "avg_score": round(avg, 1),
                "n": len(vs),
            }

    return {
        "lookback_days": lookback_days,
        "total_scored_posts": len(paired),
        "platform_avg_scores": platform_avg,
        "best_format_per_platform": best_format_per_platform,
        "best_hour_per_platform": best_hour_per_platform,
        "top_posts": top,
        "bottom_posts": bottom,
    }

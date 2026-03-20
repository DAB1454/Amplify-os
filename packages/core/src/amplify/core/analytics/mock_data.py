"""Mock metric data seeder for demo/testing without live APIs.

Generates realistic-looking daily metric snapshots for posts across
Instagram, TikTok, and YouTube over a configurable date range.
"""

from __future__ import annotations

import hashlib
import random
from datetime import date, timedelta
from typing import Any

# Reproducible randomness keyed to post_id + date
_RNG_SEED_BASE = 42


def _seed_for(post_id: str, d: date) -> int:
    """Deterministic seed so the same inputs always produce the same data."""
    h = hashlib.md5(f"{post_id}:{d.isoformat()}".encode()).hexdigest()
    return int(h[:8], 16)


# ── Platform engagement profiles ────────────────────────────────

PLATFORM_PROFILES: dict[str, dict[str, Any]] = {
    "instagram": {
        "impressions_range": (500, 8000),
        "engagement_rate_range": (0.02, 0.12),
        "ctr_range": (0.005, 0.04),
        "saves_ratio": 0.15,
        "shares_ratio": 0.08,
    },
    "tiktok": {
        "impressions_range": (1000, 50000),
        "engagement_rate_range": (0.04, 0.18),
        "ctr_range": (0.01, 0.06),
        "saves_ratio": 0.20,
        "shares_ratio": 0.12,
    },
    "youtube": {
        "impressions_range": (200, 5000),
        "engagement_rate_range": (0.01, 0.08),
        "ctr_range": (0.02, 0.10),
        "saves_ratio": 0.05,
        "shares_ratio": 0.03,
    },
}


def generate_daily_metrics(
    post_id: str,
    platform: str,
    start_date: date,
    days: int = 7,
) -> list[dict]:
    """Generate daily metric snapshots for a single post.

    Returns list of dicts with keys:
      post_id, platform, date, impressions, likes, comments, shares,
      saves, clicks, engagement_rate, click_through_rate
    """
    profile = PLATFORM_PROFILES.get(platform, PLATFORM_PROFILES["instagram"])
    results = []

    for day_offset in range(days):
        d = start_date + timedelta(days=day_offset)
        rng = random.Random(_seed_for(post_id, d))

        impressions = rng.randint(*profile["impressions_range"])

        # Engagement follows a distribution that decays over time
        decay = max(0.3, 1.0 - (day_offset * 0.08))
        eng_rate = rng.uniform(*profile["engagement_rate_range"]) * decay
        ctr = rng.uniform(*profile["ctr_range"]) * decay

        total_engagement = int(impressions * eng_rate)
        # Split engagement into likes/comments/shares/saves
        likes = int(total_engagement * 0.55)
        comments = int(total_engagement * 0.20)
        shares = int(total_engagement * profile["shares_ratio"] * 2)
        saves = int(total_engagement * profile["saves_ratio"] * 2)
        clicks = int(impressions * ctr)

        results.append({
            "post_id": post_id,
            "platform": platform,
            "date": d.isoformat(),
            "impressions": impressions,
            "likes": likes,
            "comments": comments,
            "shares": shares,
            "saves": saves,
            "clicks": clicks,
            "engagement_rate": round(eng_rate, 4),
            "click_through_rate": round(ctr, 4),
        })

    return results


def generate_campaign_metrics(
    campaign_id: str,
    posts: list[dict],
    start_date: date | None = None,
    days: int = 14,
) -> list[dict]:
    """Generate daily metrics for all posts in a campaign.

    posts: list of {"post_id": str, "platform": str}
    Returns flat list of daily metric dicts.
    """
    if start_date is None:
        start_date = date.today() - timedelta(days=days)

    all_metrics: list[dict] = []
    for post in posts:
        post_metrics = generate_daily_metrics(
            post_id=post["post_id"],
            platform=post.get("platform", "instagram"),
            start_date=start_date,
            days=days,
        )
        for m in post_metrics:
            m["campaign_id"] = campaign_id
        all_metrics.extend(post_metrics)

    return all_metrics


def generate_demo_dataset() -> dict:
    """Generate a complete demo dataset for the analyst flow.

    Returns:
        {
            "campaign_id": str,
            "posts": [...],
            "daily_metrics": [...],
            "aggregated": {post_id: {totals}},
        }
    """
    campaign_id = "demo_campaign_001"
    start = date.today() - timedelta(days=14)

    posts = [
        {"post_id": "post_ig_reel_01", "platform": "instagram"},
        {"post_id": "post_ig_carousel_02", "platform": "instagram"},
        {"post_id": "post_ig_story_03", "platform": "instagram"},
        {"post_id": "post_tt_hook_a_04", "platform": "tiktok"},
        {"post_id": "post_tt_hook_b_05", "platform": "tiktok"},
        {"post_id": "post_tt_duet_06", "platform": "tiktok"},
        {"post_id": "post_yt_teaser_07", "platform": "youtube"},
        {"post_id": "post_yt_lyric_08", "platform": "youtube"},
    ]

    daily_metrics = generate_campaign_metrics(
        campaign_id=campaign_id,
        posts=posts,
        start_date=start,
        days=14,
    )

    # Aggregate totals per post
    aggregated: dict[str, dict] = {}
    for m in daily_metrics:
        pid = m["post_id"]
        if pid not in aggregated:
            aggregated[pid] = {
                "post_id": pid,
                "platform": m["platform"],
                "impressions": 0,
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "saves": 0,
                "clicks": 0,
            }
        for k in ("impressions", "likes", "comments", "shares", "saves", "clicks"):
            aggregated[pid][k] += m[k]

    return {
        "campaign_id": campaign_id,
        "posts": posts,
        "daily_metrics": daily_metrics,
        "aggregated": aggregated,
    }

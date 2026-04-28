"""Weekly analyst job — scores posts and recommends keep/remix/stop.

Payload schema:
{
    "tenant_id": "uuid",
    "campaign_id": "uuid" (optional),
    "days": 7
}

Designed to run on a weekly schedule (e.g. every Monday).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta

from sqlalchemy import select

logger = logging.getLogger(__name__)


async def weekly_analyst(payload: dict) -> dict:
    """Generate keep/remix/stop recommendations from real post metrics."""
    from amplify.core.analytics.scoring import PostScore, score_posts
    from amplify.core.analytics.analyst import weekly_analyst_report
    from amplify.db.session import get_async_session
    from amplify.db.models.post import PostModel
    from app.config import Settings

    settings = Settings()
    tenant_id_str = payload.get("tenant_id")
    campaign_id_str = payload.get("campaign_id")
    days = payload.get("days", 7)
    end = date.today()
    start = end - timedelta(days=days)

    if not tenant_id_str:
        return {"status": "error", "error": "tenant_id required"}

    tenant_id = uuid.UUID(tenant_id_str)
    campaign_id = uuid.UUID(campaign_id_str) if campaign_id_str else None

    async with get_async_session(settings.database_url) as db:
        # Query published posts with engagement data
        post_filter = [
            PostModel.tenant_id == tenant_id,
            PostModel.status == "published",
            PostModel.published_at >= start,
        ]
        if campaign_id:
            post_filter.append(PostModel.campaign_id == campaign_id)

        stmt = select(
            PostModel.id, PostModel.platform, PostModel.engagement,
        ).where(*post_filter)
        rows = (await db.execute(stmt)).all()

    # Build scoring input from real engagement data
    posts_data = []
    for post_id, platform, engagement in rows:
        if not engagement or not isinstance(engagement, dict):
            continue
        impressions = (
            engagement.get("impressions", 0)
            or engagement.get("views", 0)
            or engagement.get("reach", 0)
            or 0
        )
        likes = engagement.get("likes", 0) or 0
        if impressions <= 0 and likes <= 0:
            continue
        posts_data.append({
            "post_id": str(post_id),
            "platform": platform or "",
            "impressions": impressions,
            "likes": likes,
            "comments": engagement.get("comments", 0) or 0,
            "shares": engagement.get("shares", 0) or 0,
            "saves": engagement.get("saves", 0) or 0,
            "clicks": engagement.get("clicks", 0) or 0,
        })

    cid = str(campaign_id) if campaign_id else "all"

    if not posts_data:
        logger.info("No published posts with engagement for tenant %s in last %d days", tenant_id, days)
        return {
            "status": "ok",
            "campaign_id": cid,
            "total_posts": 0,
            "keep": 0,
            "remix": 0,
            "stop": 0,
            "summary": f"No published posts with engagement in the last {days} day(s).",
            "verdicts": [],
        }

    scores = score_posts(posts_data)

    report = weekly_analyst_report(
        campaign_id=cid,
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        scores=scores,
    )

    logger.info(
        "Weekly analyst report for %s: %d posts — %d keep, %d remix, %d stop",
        cid,
        report.total_posts,
        report.keep_count,
        report.remix_count,
        report.stop_count,
    )

    return {
        "status": "ok",
        "campaign_id": report.campaign_id,
        "total_posts": report.total_posts,
        "keep": report.keep_count,
        "remix": report.remix_count,
        "stop": report.stop_count,
        "summary": report.summary,
        "verdicts": [
            {
                "post_id": v.post_id,
                "verdict": v.verdict.value,
                "composite_score": v.composite_score,
                "reason": v.reason,
            }
            for v in report.verdicts
        ],
    }

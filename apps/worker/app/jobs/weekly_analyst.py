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
from datetime import date, timedelta

logger = logging.getLogger(__name__)


async def weekly_analyst(payload: dict) -> dict:
    """Generate keep/remix/stop recommendations for a campaign's posts."""
    from amplify.core.analytics.mock_data import generate_demo_dataset
    from amplify.core.analytics.scoring import score_posts
    from amplify.core.analytics.analyst import weekly_analyst_report

    campaign_id = payload.get("campaign_id", "all")
    days = payload.get("days", 7)
    end = date.today()
    start = end - timedelta(days=days)

    # Use mock data for now
    demo = generate_demo_dataset()
    aggregated = list(demo["aggregated"].values())
    scores = score_posts(aggregated)

    report = weekly_analyst_report(
        campaign_id=campaign_id,
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        scores=scores,
    )

    logger.info(
        "Weekly analyst report for %s: %d posts — %d keep, %d remix, %d stop",
        campaign_id,
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

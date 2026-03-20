"""Metrics synchronization job — pulls engagement from platform APIs.

Payload schema:
{
    "tenant_id": "uuid",
    "post_ids": ["uuid", ...],       # specific posts to sync (optional)
    "platform": "instagram|tiktok|youtube",  # filter by platform (optional)
    "use_mock": true                  # use mock data for demo (default: true)
}
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)


async def sync_metrics(payload: dict) -> dict:
    """Pull engagement metrics from platform APIs and store.

    When use_mock=True (or no real adapter configured), generates
    deterministic mock metrics from the scoring module.
    """
    from amplify.core.analytics.mock_data import generate_daily_metrics

    tenant_id = payload.get("tenant_id", "")
    post_ids = payload.get("post_ids", [])
    platform = payload.get("platform", "")
    use_mock = payload.get("use_mock", True)

    if not post_ids:
        logger.info("No post_ids provided, nothing to sync")
        return {"status": "ok", "metrics_synced": 0}

    synced = 0

    if use_mock:
        # Generate mock metrics for each post
        for post_id in post_ids:
            plat = platform or "instagram"
            metrics = generate_daily_metrics(
                post_id=str(post_id),
                platform=plat,
                start_date=date.today() - timedelta(days=1),
                days=1,
            )
            synced += len(metrics)
            logger.info(
                "Mock metrics generated for post %s: %d snapshots",
                post_id,
                len(metrics),
            )
            # TODO: persist to DailyMetricModel
            # for m in metrics:
            #     await db.add(DailyMetricModel(
            #         tenant_id=tenant_id,
            #         source=plat,
            #         metric_name="engagement",
            #         entity_type="post",
            #         entity_id=post_id,
            #         date=m["date"],
            #         value=m["impressions"],
            #     ))
    else:
        # TODO: call real platform adapters
        # adapter = get_adapter(platform)
        # for post_id in post_ids:
        #     snapshot = await adapter.sync_metrics(post_id)
        #     await store_metrics(snapshot)
        #     synced += 1
        logger.warning("Real metrics sync not yet implemented")

    return {
        "status": "ok",
        "metrics_synced": synced,
        "source": "mock" if use_mock else "api",
    }

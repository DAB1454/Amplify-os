"""Scheduled post scanner — finds posts ready to publish and enqueues them.

This job runs periodically (e.g. every 60s) and picks up posts whose
scheduled_at has passed and status is 'scheduled' or 'approved'.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


async def scan_scheduled(payload: dict | None = None) -> dict:
    """Scan for scheduled posts that are ready to publish.

    In production this queries the DB for posts where:
      status IN ('scheduled', 'approved') AND scheduled_at <= NOW()

    For each matching post, enqueues a publish_post job.
    """
    # TODO: wire DB session from worker context
    # async with get_session() as db:
    #     stmt = select(PostModel).where(
    #         PostModel.status.in_(["scheduled", "approved"]),
    #         PostModel.scheduled_at <= datetime.utcnow(),
    #     )
    #     posts = (await db.execute(stmt)).scalars().all()
    #     for post in posts:
    #         post.status = "publishing"
    #         await queue.enqueue("publish_post", {
    #             "post_id": str(post.id),
    #             "tenant_id": str(post.tenant_id),
    #             "platform": post.platform,
    #             "content": post.content_text,
    #             "media_urls": post.media_urls,
    #             "destination_url": post.destination_url,
    #         })

    now = datetime.utcnow()
    logger.info("Scanning for scheduled posts ready at %s", now.isoformat())
    return {"status": "ok", "scanned_at": now.isoformat(), "posts_enqueued": 0}

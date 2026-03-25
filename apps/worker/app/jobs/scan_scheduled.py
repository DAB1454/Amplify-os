"""Scheduled post scanner — finds posts ready to publish and enqueues them.

This job runs periodically (every 60s) and picks up posts whose
scheduled_at has passed and status is 'scheduled' or 'approved'.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

logger = logging.getLogger(__name__)


async def scan_scheduled(payload: dict | None = None) -> dict:
    """Scan for scheduled posts that are ready to publish.

    Queries the DB for posts where:
      status IN ('scheduled', 'approved') AND scheduled_at <= NOW()

    For each matching post, enqueues a publish_post job.
    """
    from amplify.db.session import get_async_session
    from amplify.db.models.post import PostModel
    from app.config import Settings

    settings = Settings()
    now = datetime.utcnow()
    logger.info("Scanning for scheduled posts ready at %s", now.isoformat())

    enqueued = 0

    async with get_async_session(settings.database_url) as db:
        stmt = select(PostModel).where(
            PostModel.status.in_(["scheduled", "approved"]),
            PostModel.scheduled_at <= now,
            PostModel.scheduled_at.isnot(None),
        )
        result = await db.execute(stmt)
        posts = result.scalars().all()

        if not posts:
            logger.info("No scheduled posts due for publishing")
            return {"status": "ok", "scanned_at": now.isoformat(), "posts_enqueued": 0}

        # Enqueue publish jobs via Redis
        import redis.asyncio as aioredis
        from app.queue import JobQueue

        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        queue = JobQueue(r)

        try:
            for post in posts:
                # Transition to "publishing" so we don't pick it up again
                post.status = "publishing"

                job_payload = {
                    "post_id": str(post.id),
                    "tenant_id": str(post.tenant_id),
                    "platform": post.platform or "",
                    "channel_id": str(post.channel_id) if post.channel_id else "",
                    "content": post.content_text or "",
                    "media_urls": post.media_urls or [],
                    "destination_url": post.destination_url or "",
                    "campaign_id": str(post.campaign_id) if post.campaign_id else "",
                    "retry_count": post.retry_count or 0,
                    "max_retries": post.max_retries or 3,
                    "skip_policy": True,
                }

                await queue.enqueue(
                    "publish_post",
                    job_payload,
                    idempotency_key=f"publish_{post.id}",
                )
                enqueued += 1
                logger.info("Enqueued publish_post for post %s (%s)", post.id, post.platform)

            # Status transitions auto-commit when get_async_session exits
        finally:
            await r.aclose()

    logger.info("Scan complete: %d posts enqueued for publishing", enqueued)
    return {"status": "ok", "scanned_at": now.isoformat(), "posts_enqueued": enqueued}

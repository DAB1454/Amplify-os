"""Scheduled post scanner — finds posts ready to publish and enqueues them.

This job runs periodically (every 60s) and picks up posts whose
scheduled_at has passed and status is 'scheduled' or 'approved'.

Pre-flight: groups posts by channel and proactively refreshes tokens
before enqueuing. If a channel's token is dead (refresh fails), all
posts for that channel are marked failed with a "reconnect" message
instead of being enqueued to fail one-by-one.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select

logger = logging.getLogger(__name__)


async def _preflight_refresh_channel(
    db,
    channel,
    encryptor,
    settings,
) -> str | None:
    """Attempt to refresh a channel's token if expiring soon.

    Returns None on success, or an error message string if the channel's
    tokens are dead and the user must reconnect.
    """
    from amplify.adapters.token_store import TokenStore

    tokens = TokenStore.from_channel_connection(channel, encryptor)
    platform = channel.platform

    if not tokens.access_token:
        return f"No access token for {platform} — reconnect the channel on the Channels page."

    # Check if token is expired or expiring within 15 minutes
    buffer = timedelta(minutes=15) if platform in ("twitter", "tiktok") else timedelta(minutes=5)
    needs_refresh = tokens.expires_at and datetime.utcnow() >= (tokens.expires_at - buffer)

    if not needs_refresh:
        return None  # Token is fine

    logger.info("Pre-flight refresh for %s channel %s (expires %s)", platform, channel.id, tokens.expires_at)

    try:
        from app.services.oauth_service import OAuthService
        svc = OAuthService(db, None, settings)
        await svc.refresh_channel_token(channel.id, channel.tenant_id)
        channel.last_health_status = "valid"
        await db.flush()
        logger.info("Pre-flight refresh succeeded for %s channel %s", platform, channel.id)
        return None
    except Exception as exc:
        logger.error("Pre-flight refresh FAILED for %s channel %s: %s", platform, channel.id, exc)
        channel.last_health_status = "needs_reconnect"
        await db.flush()
        return (
            f"{platform} token expired and refresh failed — "
            f"reconnect the channel on the Channels page."
        )


async def scan_scheduled(payload: dict | None = None) -> dict:
    """Scan for scheduled posts that are ready to publish.

    Queries the DB for posts where:
      status IN ('scheduled', 'approved') AND scheduled_at <= NOW()

    Pre-flight: refreshes tokens per-channel before enqueuing.
    If refresh fails, marks all posts for that channel as failed.

    For each matching post with healthy tokens, enqueues a publish_post job.
    """
    from amplify.db.session import get_async_session
    from amplify.db.models.post import PostModel
    from amplify.db.models.channel import ChannelConnectionModel
    from amplify.adapters.crypto import TokenEncryptor
    from app.config import Settings

    settings = Settings()
    now = datetime.utcnow()
    logger.info("Scanning for scheduled posts ready at %s", now.isoformat())

    enqueued = 0
    failed_channels = 0

    async with get_async_session(settings.database_url) as db:
        stmt = select(PostModel).where(
            PostModel.status.in_(["scheduled", "approved"]),
            PostModel.scheduled_at <= now,
            PostModel.scheduled_at.isnot(None),
            PostModel.channel_id.isnot(None),
        )
        result = await db.execute(stmt)
        posts = result.scalars().all()

        if not posts:
            logger.info("No scheduled posts due for publishing")
            return {"status": "ok", "scanned_at": now.isoformat(), "posts_enqueued": 0}

        # ── Pre-flight: group by channel, refresh tokens ─────────
        encryptor = TokenEncryptor(
            primary_key=settings.token_encryption_key,
            old_keys=settings.token_encryption_old_keys,
            deployment_mode=settings.deployment_mode,
        )

        # Group posts by channel_id
        posts_by_channel: dict[uuid.UUID, list] = defaultdict(list)
        for post in posts:
            posts_by_channel[post.channel_id].append(post)

        # Check each channel's token health and refresh if needed
        dead_channels: dict[uuid.UUID, str] = {}  # channel_id → error message
        for channel_id, channel_posts in posts_by_channel.items():
            ch_result = await db.execute(
                select(ChannelConnectionModel).where(ChannelConnectionModel.id == channel_id)
            )
            channel = ch_result.scalar_one_or_none()
            if channel is None:
                dead_channels[channel_id] = "Channel not found — it may have been deleted."
                continue

            error = await _preflight_refresh_channel(db, channel, encryptor, settings)
            if error:
                dead_channels[channel_id] = error
                failed_channels += 1

        # Fail all posts for dead channels immediately
        for channel_id, error_msg in dead_channels.items():
            for post in posts_by_channel[channel_id]:
                post.status = "failed"
                post.last_error = error_msg[:1000]
                logger.warning(
                    "Post %s failed (channel %s token dead): %s",
                    post.id, channel_id, error_msg,
                )

        # ── Enqueue healthy posts via Redis ──────────────────────
        import redis.asyncio as aioredis
        from app.queue import JobQueue

        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        queue = JobQueue(r)

        try:
            for post in posts:
                # Skip posts whose channels failed pre-flight
                if post.channel_id in dead_channels:
                    continue
                retry_count = post.retry_count or 0
                job_payload = {
                    "post_id": str(post.id),
                    "tenant_id": str(post.tenant_id),
                    "platform": post.platform or "",
                    "channel_id": str(post.channel_id) if post.channel_id else "",
                    "content": post.content_text or "",
                    "media_urls": post.media_urls or [],
                    "destination_url": post.destination_url or "",
                    "campaign_id": str(post.campaign_id) if post.campaign_id else "",
                    "retry_count": retry_count,
                    "max_retries": post.max_retries or 3,
                    "skip_policy": True,
                }

                # Idempotency key includes retry_count so each retry gets a fresh
                # dedup slot. Without this, a failed publish leaves a 1h dedup
                # ghost that blocks all subsequent retries.
                # 10-minute timeout: Instagram container processing can take
                # 4-5 min on its own; the default 5-min worker timeout was
                # racing with IG's poll loop and producing duplicate publishes
                # on retry. Give the job real headroom.
                job_id = await queue.enqueue(
                    "publish_post",
                    job_payload,
                    idempotency_key=f"publish_{post.id}_{retry_count}",
                    timeout_seconds=600,
                )

                # Only flip status to "publishing" if the job was actually
                # enqueued. If enqueue returned None (dedup hit), leave the post
                # in its current state so the next scan can try again — never
                # flip a post into "publishing" without a real job behind it.
                if job_id is None:
                    logger.info(
                        "Skipped post %s — job already in flight (dedup)", post.id
                    )
                    continue

                post.status = "publishing"
                enqueued += 1
                logger.info("Enqueued publish_post for post %s (%s)", post.id, post.platform)

            # Status transitions auto-commit when get_async_session exits
        finally:
            await r.aclose()

    logger.info(
        "Scan complete: %d posts enqueued, %d channels failed pre-flight",
        enqueued, failed_channels,
    )
    return {
        "status": "ok",
        "scanned_at": now.isoformat(),
        "posts_enqueued": enqueued,
        "channels_failed_preflight": failed_channels,
    }

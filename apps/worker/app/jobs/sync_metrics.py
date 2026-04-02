"""Metrics synchronization job — pulls engagement from platform APIs.

Runs on a schedule to sync metrics for all published posts that have a
platform_post_id. Populates both:
  - PostModel.engagement (JSON) — used by post scores / analyst
  - DailyMetricModel rows   — used by time-series charts

Payload schema (all fields optional):
{
    "tenant_id": "uuid",           # sync one tenant (default: all tenants)
    "post_ids": ["uuid", ...],     # specific posts (default: all published)
    "platform": "instagram|...",   # filter by platform (default: all)
}
"""

from __future__ import annotations

import importlib
import logging
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import select, or_

logger = logging.getLogger(__name__)

ADAPTER_MAP = {
    "instagram": "amplify.adapters.instagram.adapter.InstagramAdapter",
    "youtube": "amplify.adapters.youtube.adapter.YouTubeAdapter",
    "tiktok": "amplify.adapters.tiktok.adapter.TikTokAdapter",
}

# Only sync posts published in the last N days (older posts stop changing)
MAX_POST_AGE_DAYS = 30


async def sync_metrics(payload: dict | None = None) -> dict:
    """Pull engagement metrics from platform APIs and store."""
    from amplify.db.session import get_async_session
    from amplify.db.models.post import PostModel
    from amplify.db.models.channel import ChannelConnectionModel
    from amplify.db.models.metric import DailyMetricModel
    from amplify.adapters.crypto import TokenEncryptor
    from amplify.adapters.token_store import TokenStore
    from app.config import Settings

    payload = payload or {}
    settings = Settings()
    cutoff = datetime.utcnow() - timedelta(days=MAX_POST_AGE_DAYS)

    synced = 0
    errors = 0

    async with get_async_session(settings.database_url) as db:
        # Build query for published posts with a platform_post_id
        filters = [
            PostModel.status.in_(["published", "pending_approval"]),
            PostModel.platform_post_id.isnot(None),
            PostModel.platform_post_id != "",
            or_(
                PostModel.published_at >= cutoff,
                PostModel.published_at.is_(None),
            ),
        ]

        # Optional filters
        if payload.get("tenant_id"):
            filters.append(PostModel.tenant_id == uuid.UUID(payload["tenant_id"]))
        if payload.get("platform"):
            filters.append(PostModel.platform == payload["platform"])
        if payload.get("post_ids"):
            filters.append(PostModel.id.in_([uuid.UUID(p) for p in payload["post_ids"]]))

        stmt = (
            select(PostModel)
            .where(*filters)
            .order_by(PostModel.published_at.desc())
            .limit(200)  # cap per run
        )
        result = await db.execute(stmt)
        posts = list(result.scalars().all())

        if not posts:
            logger.info("No published posts to sync metrics for")
            return {"status": "ok", "synced": 0, "errors": 0}

        logger.info("Syncing metrics for %d published posts", len(posts))

        # Group posts by channel_id so we can reuse adapter connections
        channel_posts: dict[str, list] = {}
        for post in posts:
            if post.channel_id:
                key = str(post.channel_id)
                channel_posts.setdefault(key, []).append(post)

        encryptor = TokenEncryptor(
            primary_key=settings.token_encryption_key,
            old_keys=settings.token_encryption_old_keys,
            deployment_mode=settings.deployment_mode,
        )

        for channel_id_str, channel_posts_list in channel_posts.items():
            adapter = None
            try:
                # Load channel + tokens
                ch_result = await db.execute(
                    select(ChannelConnectionModel).where(
                        ChannelConnectionModel.id == uuid.UUID(channel_id_str)
                    )
                )
                channel = ch_result.scalar_one_or_none()
                if not channel or not channel.is_active:
                    logger.warning("Channel %s not found or inactive, skipping", channel_id_str)
                    continue

                platform = channel.platform
                if platform not in ADAPTER_MAP:
                    continue

                tokens = TokenStore.from_channel_connection(channel, encryptor)
                if not tokens.access_token:
                    logger.warning("No access token for channel %s, skipping", channel_id_str)
                    continue

                # Instantiate adapter
                module_path, class_name = ADAPTER_MAP[platform].rsplit(".", 1)
                module = importlib.import_module(module_path)
                adapter_class = getattr(module, class_name)
                adapter = adapter_class(dry_run=settings.is_local)
                await adapter.connect({
                    "access_token": tokens.access_token,
                    "refresh_token": tokens.refresh_token,
                    "token_expires_at": tokens.expires_at,
                    "account_id": tokens.account_id,
                })

                # Sync each post
                for post in channel_posts_list:
                    try:
                        snapshot = await adapter.sync_metrics(post.platform_post_id)

                        # Update PostModel.engagement JSON
                        post.engagement = {
                            "impressions": snapshot.impressions,
                            "reach": snapshot.reach,
                            "likes": snapshot.likes,
                            "comments": snapshot.comments,
                            "shares": snapshot.shares,
                            "saves": snapshot.saves,
                            "views": snapshot.views,
                            "clicks": snapshot.clicks,
                            "synced_at": datetime.utcnow().isoformat(),
                        }

                        # Upsert DailyMetricModel rows for time-series
                        today = date.today()
                        metric_pairs = [
                            ("impressions", snapshot.impressions),
                            ("engagement", snapshot.likes + snapshot.comments + snapshot.shares + snapshot.saves),
                            ("views", snapshot.views),
                            ("reach", snapshot.reach),
                        ]
                        for metric_name, value in metric_pairs:
                            if value <= 0:
                                continue
                            # Check for existing row (upsert)
                            existing = await db.execute(
                                select(DailyMetricModel).where(
                                    DailyMetricModel.tenant_id == post.tenant_id,
                                    DailyMetricModel.source == platform,
                                    DailyMetricModel.metric_name == metric_name,
                                    DailyMetricModel.entity_type == "post",
                                    DailyMetricModel.entity_id == post.id,
                                    DailyMetricModel.date == today,
                                )
                            )
                            row = existing.scalar_one_or_none()
                            if row:
                                row.previous_value = row.value
                                row.value = float(value)
                            else:
                                db.add(DailyMetricModel(
                                    tenant_id=post.tenant_id,
                                    source=platform,
                                    metric_name=metric_name,
                                    entity_type="post",
                                    entity_id=post.id,
                                    date=today,
                                    value=float(value),
                                ))

                        synced += 1
                        logger.info(
                            "Synced metrics for post %s (%s): impressions=%d likes=%d views=%d",
                            post.id, platform,
                            snapshot.impressions, snapshot.likes, snapshot.views,
                        )

                    except Exception as exc:
                        errors += 1
                        logger.warning(
                            "Failed to sync metrics for post %s: %s", post.id, exc
                        )

            except Exception as exc:
                errors += 1
                logger.error(
                    "Failed to connect adapter for channel %s: %s",
                    channel_id_str, exc,
                )
            finally:
                if adapter and hasattr(adapter, "close"):
                    try:
                        await adapter.close()
                    except Exception:
                        pass

    logger.info("Metrics sync complete: %d synced, %d errors", synced, errors)
    return {
        "status": "ok",
        "synced": synced,
        "errors": errors,
        "source": "api",
    }

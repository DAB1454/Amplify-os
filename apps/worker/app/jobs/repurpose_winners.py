"""Auto-repurpose winning posts to other channels.

Runs daily after pattern updates. Finds published posts scoring above
the KEEP threshold (composite_score >= 70) that haven't already been
repurposed, and creates draft copies on the tenant's other active
channels. This tests whether performance was content-specific or
channel-specific.

Respects autonomy level:
- manual / assisted: creates drafts (user must queue + approve)
- auto_campaigns+: creates drafts and auto-queues them
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func, and_

from amplify.db.session import get_async_session
from amplify.db.models.post import PostModel
from amplify.db.models.channel import ChannelConnectionModel
from amplify.db.models.tenant import TenantModel
from amplify.core.analytics.scoring import score_posts
from amplify.observability.metrics import MetricsCollector

logger = logging.getLogger(__name__)
metrics = MetricsCollector()

# Posts must score at or above this threshold to be auto-repurposed
KEEP_THRESHOLD = 70.0

# Maximum repurposed drafts to create per tenant per run
MAX_PER_TENANT = 10

# Only consider posts published in the last N days
LOOKBACK_DAYS = 14


async def repurpose_winners(payload: dict) -> dict:
    """Find winning posts and create cross-channel draft copies."""
    from app.config import Settings

    settings = Settings()
    specific_tenant = payload.get("tenant_id")

    async with get_async_session(settings.database_url) as db:
        # Get tenants with 2+ active channels (need at least 2 to cross-post)
        if specific_tenant:
            tenant_ids = [uuid.UUID(specific_tenant)]
        else:
            stmt = (
                select(ChannelConnectionModel.tenant_id)
                .where(ChannelConnectionModel.is_active.is_(True))
                .group_by(ChannelConnectionModel.tenant_id)
                .having(func.count(ChannelConnectionModel.id) >= 2)
            )
            result = await db.execute(stmt)
            tenant_ids = [row[0] for row in result.all()]

        total_created = 0
        total_skipped = 0
        errors = 0

        for tid in tenant_ids:
            try:
                created, skipped = await _repurpose_for_tenant(db, tid)
                total_created += created
                total_skipped += skipped
                await db.commit()
            except Exception:
                logger.exception("repurpose_winners failed for tenant %s", tid)
                await db.rollback()
                errors += 1

    metrics.gauge("repurpose.drafts_created", total_created)
    metrics.gauge("repurpose.skipped", total_skipped)
    logger.info(
        "repurpose_winners: created %d drafts, skipped %d, %d errors across %d tenants",
        total_created, total_skipped, errors, len(tenant_ids),
    )
    return {
        "tenants": len(tenant_ids),
        "created": total_created,
        "skipped": total_skipped,
        "errors": errors,
    }


async def _repurpose_for_tenant(db, tenant_id: uuid.UUID) -> tuple[int, int]:
    """Score recent posts for one tenant and repurpose winners."""

    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    # Load published posts with engagement data
    post_stmt = select(
        PostModel.id,
        PostModel.platform,
        PostModel.engagement,
        PostModel.channel_id,
        PostModel.content_text,
        PostModel.media_urls,
        PostModel.campaign_id,
        PostModel.destination_url,
        PostModel.action_type_label,
        PostModel.goal,
        PostModel.track_reference,
        PostModel.day_number,
    ).where(
        PostModel.tenant_id == tenant_id,
        PostModel.status == "published",
        PostModel.published_at >= cutoff,
    )
    rows = (await db.execute(post_stmt)).all()

    if not rows:
        return 0, 0

    # Build scoring input — same normalization as analytics_service
    posts_data = []
    post_lookup: dict[str, dict] = {}
    for row in rows:
        (post_id, platform, engagement, channel_id,
         content_text, media_urls, campaign_id, destination_url,
         action_type_label, goal, track_reference, day_number) = row

        if not engagement:
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

        pid = str(post_id)
        posts_data.append({
            "post_id": pid,
            "platform": platform,
            "impressions": impressions,
            "likes": likes,
            "comments": engagement.get("comments", 0) or 0,
            "shares": engagement.get("shares", 0) or 0,
            "saves": engagement.get("saves", 0) or 0,
            "clicks": engagement.get("clicks", 0) or 0,
        })
        post_lookup[pid] = {
            "post_id": post_id,
            "platform": platform,
            "channel_id": channel_id,
            "content_text": content_text,
            "media_urls": media_urls,
            "campaign_id": campaign_id,
            "destination_url": destination_url,
            "action_type_label": action_type_label,
            "goal": goal,
            "track_reference": track_reference,
            "day_number": day_number,
        }

    if not posts_data:
        return 0, 0

    # Score
    scores = score_posts(posts_data)
    winners = [s for s in scores if s.composite_score >= KEEP_THRESHOLD]

    if not winners:
        return 0, 0

    # Load active channels for this tenant
    ch_stmt = select(ChannelConnectionModel).where(
        ChannelConnectionModel.tenant_id == tenant_id,
        ChannelConnectionModel.is_active.is_(True),
    )
    channels = list((await db.execute(ch_stmt)).scalars().all())
    channel_by_platform: dict[str, ChannelConnectionModel] = {}
    for ch in channels:
        # Take the first active channel per platform
        if ch.platform not in channel_by_platform:
            channel_by_platform[ch.platform] = ch

    if len(channel_by_platform) < 2:
        return 0, 0

    # Check which posts have already been repurposed (to any channel)
    already_repurposed_stmt = (
        select(PostModel.repurposed_from_id)
        .where(
            PostModel.tenant_id == tenant_id,
            PostModel.repurposed_from_id.isnot(None),
        )
        .distinct()
    )
    already = set(
        row[0] for row in (await db.execute(already_repurposed_stmt)).all()
    )

    # Check tenant autonomy level for auto-queuing
    tenant_stmt = select(TenantModel.automation_level).where(
        TenantModel.id == tenant_id
    )
    automation_level = (await db.execute(tenant_stmt)).scalar_one_or_none() or "manual"
    auto_queue = automation_level in ("auto_campaigns", "autonomous")

    created = 0
    skipped = 0

    # Sort winners by score descending, cap per tenant
    winners.sort(key=lambda s: s.composite_score, reverse=True)

    for score in winners:
        if created >= MAX_PER_TENANT:
            break

        source = post_lookup.get(score.post_id)
        if not source:
            continue

        source_post_id = source["post_id"]
        if source_post_id in already:
            skipped += 1
            continue

        source_platform = source["platform"]

        # Create a draft on each OTHER platform
        for target_platform, target_channel in channel_by_platform.items():
            if target_platform == source_platform:
                continue
            if created >= MAX_PER_TENANT:
                break

            # Map format to something appropriate for the target platform
            target_format = _adapt_format(
                source["action_type_label"], source_platform, target_platform
            )

            new_post = PostModel(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                campaign_id=source["campaign_id"],
                channel_id=target_channel.id,
                platform=target_platform,
                status="draft" if not auto_queue else "queued",
                content_text=source["content_text"] or "",
                media_urls=list(source["media_urls"] or []),
                destination_url=source["destination_url"],
                action_type_label=target_format,
                goal=source["goal"],
                track_reference=source["track_reference"],
                day_number=source["day_number"],
                repurposed_from_id=source_post_id,
            )
            db.add(new_post)
            created += 1

            logger.info(
                "Auto-repurposed post %s (%s, score=%.0f) → %s as %s [%s]",
                source_post_id, source_platform, score.composite_score,
                target_platform, target_format, new_post.status,
            )

        # Mark this source as processed so we don't re-repurpose next run
        already.add(source_post_id)

    return created, skipped


def _adapt_format(
    source_format: str | None, source_platform: str, target_platform: str
) -> str:
    """Map a source post's format to an appropriate format on the target platform.

    e.g. YouTube Short → Instagram Reel → TikTok Reel
         Instagram Carousel → Facebook post (no carousel support via API)
         YouTube Video → Instagram Reel (clips to short form)
    """
    src = (source_format or "").lower().strip()

    # Short-form video mappings (the most common cross-post)
    short_form = {"reel", "reels", "short", "shorts"}
    if src in short_form:
        if target_platform == "youtube":
            return "short"
        if target_platform in ("instagram", "tiktok"):
            return "reel"
        return "post"

    # Full-length video → short-form on other platforms
    if src in ("video", "long_video", "full_video", "official_video"):
        if target_platform == "youtube":
            return "video"
        # Other platforms get short-form (content pipeline will clip)
        return "reel"

    # Lyric video → reel everywhere
    if "lyric" in src:
        if target_platform == "youtube":
            return "short"
        return "reel"

    # Carousel → only IG supports it; others get a feed post
    if "carousel" in src:
        if target_platform == "instagram":
            return "carousel"
        return "post"

    # Story → story on IG, reel on TikTok (no stories), short on YT
    if "story" in src:
        if target_platform == "instagram":
            return "story"
        if target_platform == "youtube":
            return "short"
        return "reel"

    # Static / feed post → same on all platforms
    if src in ("post", "static", "feed", ""):
        return "post"

    # Default: keep the source format
    return src

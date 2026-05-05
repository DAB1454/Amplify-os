"""Calendar ingestion job — creates draft posts from calendar items.

When calendar items exist without corresponding posts, this job
materializes draft posts for them so the content generation pipeline
can fill in AI captions and attach media.

Can be triggered:
- After CSV import (API enqueues this job)
- After plan generation (to backfill any calendar items the planner
  created without posts)
- Periodically to catch manual calendar entries

Payload schema:
{
    "tenant_id": "uuid" (required),
    "campaign_id": "uuid" (optional — scope to one campaign),
    "calendar_item_ids": ["uuid", ...] (optional — process specific items)
}
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def ingest_calendar(payload: dict) -> dict:
    """Create draft posts from calendar items that don't have corresponding posts."""
    from amplify.db.session import get_async_session
    from amplify.db.models.calendar_item import CalendarItemModel
    from amplify.db.models.post import PostModel
    from amplify.db.models.channel import ChannelConnectionModel
    from amplify.db.models.campaign import CampaignModel
    from app.config import Settings

    settings = Settings()

    tenant_id_str = payload.get("tenant_id")
    if not tenant_id_str:
        return {"status": "error", "error": "tenant_id required"}

    tenant_id = uuid.UUID(tenant_id_str)
    campaign_id = uuid.UUID(payload["campaign_id"]) if payload.get("campaign_id") else None
    specific_ids = [uuid.UUID(i) for i in payload.get("calendar_item_ids", [])]
    timezone = payload.get("timezone", "America/New_York")

    posts_created = 0
    skipped = 0
    errors = 0

    async with get_async_session(settings.database_url) as db:
        # ── Find calendar items without posts ─────────────────────
        stmt = select(CalendarItemModel).where(
            CalendarItemModel.tenant_id == tenant_id,
            CalendarItemModel.is_completed == False,
        )
        if campaign_id:
            stmt = stmt.where(CalendarItemModel.campaign_id == campaign_id)
        if specific_ids:
            stmt = stmt.where(CalendarItemModel.id.in_(specific_ids))

        stmt = stmt.order_by(CalendarItemModel.scheduled_date.asc())
        result = await db.execute(stmt)
        items = list(result.scalars().all())

        if not items:
            return {"status": "ok", "events_synced": 0, "message": "No calendar items to process"}

        # ── Find existing posts to avoid duplicates ───────────────
        # Check which calendar items already have posts by matching
        # campaign_id + day_number + action_type_label.
        existing_posts = set()
        for item in items:
            if item.campaign_id:
                post_check = await db.execute(
                    select(PostModel.id).where(
                        PostModel.campaign_id == item.campaign_id,
                        PostModel.tenant_id == tenant_id,
                        PostModel.action_type_label == item.item_type,
                        PostModel.scheduled_at.isnot(None),
                    )
                )
                # Check if any post is scheduled on the same date
                for post_row in post_check.scalars().all():
                    existing_posts.add(post_row)

        # ── Load channel map for platform routing ─────────────────
        ch_result = await db.execute(
            select(ChannelConnectionModel).where(
                ChannelConnectionModel.tenant_id == tenant_id,
                ChannelConnectionModel.is_active == True,
            )
        )
        channels_by_platform: dict[str, ChannelConnectionModel] = {}
        for ch in ch_result.scalars().all():
            if ch.platform not in channels_by_platform:
                channels_by_platform[ch.platform] = ch

        # Default platform if none specified
        default_platform = next(iter(channels_by_platform), "instagram")

        # Whether this tenant's autonomy level lets us pre-schedule posts
        # straight from the planner instead of dumping drafts. Computed
        # once per ingest run rather than per-item.
        from amplify.db.models.tenant import TenantModel
        tenant_row = await db.execute(
            select(TenantModel.automation_level).where(TenantModel.id == tenant_id)
        )
        auto_level = (tenant_row.scalar_one_or_none() or "manual").lower()
        auto_schedule_enabled = auto_level in ("auto_campaigns", "autonomous")

        # ── Non-post types that don't need draft posts ────────────
        NON_POST_TYPES = {"live", "engagement", "email", "meeting", "reminder"}

        # ── Create draft posts ────────────────────────────────────
        for idx, item in enumerate(items):
            try:
                if item.item_type and item.item_type.lower().strip() in NON_POST_TYPES:
                    skipped += 1
                    continue

                # Determine platform from item type or description
                platform = _extract_platform(item) or default_platform
                channel = channels_by_platform.get(platform)
                if not channel:
                    # Fall back to any active channel
                    channel = next(iter(channels_by_platform.values()), None)
                    if channel:
                        platform = channel.platform

                # Compute scheduled_at in UTC
                scheduled_at = _compute_scheduled_at(
                    item.scheduled_date,
                    item.scheduled_time,
                    timezone,
                )

                # Determine campaign mode for approval status
                approval_status = "pending_review"
                campaign_mode = "manual"
                if item.campaign_id:
                    camp_result = await db.execute(
                        select(CampaignModel.mode).where(
                            CampaignModel.id == item.campaign_id
                        )
                    )
                    campaign_mode = camp_result.scalar_one_or_none() or "manual"
                    if campaign_mode == "autopilot":
                        approval_status = "approved"

                # Auto-schedule when the tenant is on a high-enough autonomy
                # rung. This is the cleanest place for "auto-campaigns" to
                # mean something — without it the planner just dumps drafts
                # the user has to manually walk into scheduled.
                # Requires: scheduled_at present (otherwise SCHEDULED makes
                # no sense) and either campaign mode=autopilot or tenant
                # automation_level in (auto_campaigns, autonomous).
                lifecycle_status = "draft"
                if scheduled_at and (
                    campaign_mode == "autopilot"
                    or auto_schedule_enabled
                ):
                    lifecycle_status = "scheduled"

                post = PostModel(
                    tenant_id=tenant_id,
                    campaign_id=item.campaign_id,
                    channel_id=channel.id if channel else None,
                    platform=platform,
                    status=lifecycle_status,
                    content_text=item.description or item.title or "",
                    media_urls=[],
                    scheduled_at=scheduled_at,
                    approval_status=approval_status,
                    action_type_label=item.item_type or "post",
                )
                db.add(post)
                posts_created += 1

            except Exception as exc:
                logger.warning(
                    "Failed to create post for calendar item %s: %s",
                    item.id, exc,
                )
                errors += 1

        if posts_created:
            await db.flush()

    logger.info(
        "Calendar ingestion complete: %d posts created, %d skipped, %d errors",
        posts_created, skipped, errors,
    )
    return {
        "status": "ok",
        "events_synced": posts_created,
        "skipped": skipped,
        "errors": errors,
    }


def _extract_platform(item) -> str | None:
    """Try to infer the target platform from a calendar item."""
    text = f"{item.title or ''} {item.description or ''} {item.item_type or ''}".lower()
    for platform in ("instagram", "youtube", "tiktok", "twitter"):
        if platform in text:
            return platform
    # Common aliases
    if "reel" in text or "story" in text or "carousel" in text:
        return "instagram"
    if "short" in text:
        return "youtube"
    return None


def _compute_scheduled_at(
    scheduled_date,
    scheduled_time,
    timezone: str,
) -> datetime | None:
    """Convert a date + optional time into a UTC datetime."""
    if not scheduled_date:
        return None
    try:
        t = scheduled_time or time(12, 0)  # Default to noon
        tz = ZoneInfo(timezone)
        local_dt = datetime.combine(scheduled_date, t, tzinfo=tz)
        utc_dt = local_dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        return utc_dt
    except (ValueError, TypeError):
        return None

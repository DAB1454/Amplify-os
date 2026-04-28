"""Check active campaigns and mark them completed when all posts are done."""

from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy import func, select

logger = logging.getLogger(__name__)

# Posts in these statuses are still "in progress"
_IN_PROGRESS = {"draft", "queued", "approved", "scheduled", "publishing"}


async def check_campaigns(payload: dict | None = None) -> dict:
    """Find active campaigns that should be marked completed.

    A campaign is completed when:
      1. It is currently 'active', AND
      2. All posts reached a terminal status (published/failed/pending_approval), OR
      3. Its end_date has passed (regardless of in-progress posts —
         unpublished posts stay visible on the campaign detail page).
    """
    from amplify.db.session import get_async_session
    from amplify.db.models.campaign import CampaignModel
    from amplify.db.models.post import PostModel
    from app.config import Settings

    settings = Settings()
    today = date.today()
    logger.info("Checking active campaigns for completion")

    completed_ids: list[str] = []

    async with get_async_session(settings.database_url) as db:
        stmt = select(CampaignModel).where(CampaignModel.status == "active")
        result = await db.execute(stmt)
        campaigns = result.scalars().all()

        if not campaigns:
            logger.info("No active campaigns to check")
            return {"status": "ok", "completed": 0}

        for campaign in campaigns:
            total_stmt = (
                select(func.count())
                .select_from(PostModel)
                .where(PostModel.campaign_id == campaign.id)
            )
            in_progress_stmt = (
                select(func.count())
                .select_from(PostModel)
                .where(
                    PostModel.campaign_id == campaign.id,
                    PostModel.status.in_(_IN_PROGRESS),
                )
            )

            total_count = (await db.execute(total_stmt)).scalar() or 0
            in_progress_count = (await db.execute(in_progress_stmt)).scalar() or 0

            should_complete = False

            if total_count > 0 and in_progress_count == 0:
                should_complete = True
                reason = "all posts finished"
            elif campaign.end_date and campaign.end_date < today:
                should_complete = True
                reason = f"end_date {campaign.end_date} passed ({in_progress_count} posts still pending)"

            if should_complete:
                campaign.status = "completed"
                completed_ids.append(str(campaign.id))
                logger.info(
                    "Campaign %s (%s) → completed (%s, %d posts)",
                    campaign.id,
                    campaign.name,
                    reason,
                    total_count,
                )
                await _notify_campaign_complete(
                    db, campaign.tenant_id, campaign.id,
                    campaign.name or "Untitled", total_count, reason,
                )

    logger.info("Campaign check done: %d campaigns completed", len(completed_ids))
    return {
        "status": "ok",
        "checked_at": datetime.utcnow().isoformat(),
        "completed": len(completed_ids),
        "completed_ids": completed_ids,
    }


async def _notify_campaign_complete(
    db,
    tenant_id,
    campaign_id,
    campaign_name: str,
    total_posts: int,
    reason: str,
) -> None:
    """Send in-app notification when a campaign completes."""
    from amplify.core.services.notification_service import NotificationService

    try:
        svc = NotificationService(db, tenant_id)
        await svc.notify_tenant(
            event_type="campaign.complete",
            title=f"Campaign \"{campaign_name}\" completed",
            body=f"All {total_posts} posts have finished ({reason}).",
            severity="success",
            url=f"/campaigns/{campaign_id}",
            entity_type="campaign",
            entity_id=campaign_id,
        )
    except Exception:
        logger.warning("Failed to send campaign completion notification for %s", campaign_id)

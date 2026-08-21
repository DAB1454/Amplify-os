"""Content generation job — generates AI captions and attaches media to draft posts.

Per-post work is delegated to `amplify.agents.pipeline.content.generate_content_for_post`,
which is the same code path the API's `/ai/generate-content` endpoint and the
repair sweep use. That includes the caption validator (sibling-track detection
+ empty-body guard + retries) and the full asset/clip matching logic.

This job exists to:
1. Resolve campaign-level scope (find eligible draft posts)
2. Loop through them, calling the shared pipeline per-post
3. Emit a single "content.ready" notification when done

Triggered by:
- "Generate Content" button in the campaign UI (campaigns route enqueues this)
- automation_tick for autopilot campaigns
- Manually via the worker CLI
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

logger = logging.getLogger(__name__)

# Posts with content_text shorter than this are treated as briefs
# that need AI-generated captions. Mirrors the threshold in
# amplify.agents.pipeline.content.generate_content_for_post.
BRIEF_THRESHOLD = 500


async def generate_content(payload: dict) -> dict:
    """Use AI agents to generate marketing copy for draft posts.

    Expected payload:
        campaign_id: str (required)
        tenant_id: str (required)
        user_id: str (optional)
        post_ids: list[str] (optional — if omitted, processes all eligible drafts)
    """
    from amplify.db.session import get_async_session
    from amplify.db.models.campaign import CampaignModel
    from amplify.db.models.post import PostModel
    from amplify.agents.pipeline.content import generate_content_for_post
    from app.config import Settings

    settings = Settings()

    campaign_id_str = payload.get("campaign_id")
    tenant_id_str = payload.get("tenant_id")
    if not campaign_id_str or not tenant_id_str:
        return {"status": "error", "error": "campaign_id and tenant_id required"}

    campaign_id = uuid.UUID(campaign_id_str)
    tenant_id = uuid.UUID(tenant_id_str)
    user_id = uuid.UUID(payload["user_id"]) if payload.get("user_id") else None
    specific_post_ids = [uuid.UUID(p) for p in payload.get("post_ids", [])]

    generated = 0
    assets_attached = 0
    failed = 0
    skipped = 0
    campaign_name = ""

    async with get_async_session(settings.database_url) as db:
        camp_result = await db.execute(
            select(CampaignModel).where(
                CampaignModel.id == campaign_id,
                CampaignModel.tenant_id == tenant_id,
            )
        )
        campaign = camp_result.scalar_one_or_none()
        if campaign is None:
            return {"status": "error", "error": f"Campaign {campaign_id} not found"}
        campaign_name = campaign.name

        stmt = select(PostModel).where(
            PostModel.campaign_id == campaign_id,
            PostModel.tenant_id == tenant_id,
            PostModel.status == "draft",
        )
        if specific_post_ids:
            stmt = stmt.where(PostModel.id.in_(specific_post_ids))
        posts_result = await db.execute(stmt)
        posts = posts_result.scalars().all()

        eligible = [
            p for p in posts
            if not p.content_text or len(p.content_text or "") < BRIEF_THRESHOLD
        ]

        if not eligible:
            logger.info("No eligible draft posts for campaign %s", campaign_id)
            return {
                "status": "ok",
                "pieces_generated": 0,
                "message": "No drafts need content",
            }

        logger.info(
            "Generating content for %d posts in campaign %s (%s)",
            len(eligible), campaign_id, campaign_name,
        )

        for post in eligible:
            try:
                result = await generate_content_for_post(
                    db, post.id, tenant_id, user_id,
                )
                # Commit per-post so FK share locks on the shared campaign /
                # track / release rows release immediately instead of being
                # held across every post's LLM caption call. A single
                # transaction spanning the whole loop (100s+ of Anthropic
                # round-trips) is what deadlocked against concurrent
                # generate-media writes to the same campaign's posts.
                await db.commit()
                if result.get("caption_generated"):
                    generated += 1
                elif result.get("caption_error"):
                    failed += 1
                else:
                    skipped += 1
                if result.get("assets_attached") or result.get("clip_library_used"):
                    assets_attached += 1
            except Exception as exc:
                logger.warning(
                    "Content generation failed for post %s: %s", post.id, exc,
                )
                # Roll back only the failed post's work so the next post
                # starts from a clean transaction rather than a poisoned one.
                await db.rollback()
                failed += 1

    logger.info(
        "Content generation complete for campaign %s: %d generated, %d assets, %d failed, %d skipped",
        campaign_id, generated, assets_attached, failed, skipped,
    )

    if generated > 0:
        await _notify_content_ready(tenant_id, campaign_id, campaign_name, generated)

    return {
        "status": "ok",
        "pieces_generated": generated,
        "assets_attached": assets_attached,
        "failed": failed,
        "skipped": skipped,
    }


async def _notify_content_ready(
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    campaign_name: str,
    count: int,
) -> None:
    """Send in-app notification when AI content is ready for review."""
    from amplify.db.session import get_async_session
    from amplify.core.services.notification_service import NotificationService
    from app.config import Settings

    settings = Settings()
    try:
        async with get_async_session(settings.database_url) as db:
            svc = NotificationService(db, tenant_id)
            await svc.notify_tenant(
                event_type="content.ready",
                title=f"{count} new AI-generated post{'s' if count != 1 else ''} ready",
                body=f"Campaign \"{campaign_name}\" has {count} new draft{'s' if count != 1 else ''} "
                     f"with AI-generated captions ready for your review.",
                severity="info",
                url=f"/campaigns/{campaign_id}",
                entity_type="campaign",
                entity_id=campaign_id,
            )
    except Exception:
        logger.warning(
            "Failed to send content-ready notification for campaign %s", campaign_id,
        )

"""Content generation job — generates AI captions and attaches media to draft posts.

When a campaign has draft posts (created by the planner), this job:
1. Loads campaign context (artist, release, tracks, channels)
2. Finds draft posts that still have brief-style content (short text, no real caption)
3. Calls ContentAgent.generate_caption() to produce 3 variants per post
4. Picks the best variant and updates the post's content_text
5. Finds matching assets from the library and attaches media

Can be triggered:
- By the "Generate Content" button in the campaign UI
- By the automation_tick job for autopilot campaigns
- Manually via the worker CLI
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Posts with content_text shorter than this are treated as briefs
# that need AI-generated captions.
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

    async with get_async_session(settings.database_url) as db:
        # ── Load campaign context ─────────────────────────────────
        ctx = await _load_campaign_context(db, campaign_id, tenant_id)
        if ctx is None:
            return {"status": "error", "error": f"Campaign {campaign_id} not found"}

        # ── Find eligible draft posts ─────────────────────────────
        posts = await _find_eligible_posts(
            db, campaign_id, tenant_id, specific_post_ids
        )
        if not posts:
            logger.info("No eligible draft posts for campaign %s", campaign_id)
            return {"status": "ok", "pieces_generated": 0, "message": "No drafts need content"}

        logger.info(
            "Generating content for %d posts in campaign %s (%s)",
            len(posts), campaign_id, ctx["campaign_name"],
        )

        # ── Build the ContentAgent ────────────────────────────────
        from amplify.agents.runtime.agent_runner import AgentRunner
        from amplify.agents.runtime.config import AgentConfig
        from amplify.agents.subagents.content_agent import ContentAgent

        runner = AgentRunner(config=AgentConfig())
        agent = ContentAgent(runner)

        # ── Generate content for each post ────────────────────────
        for post in posts:
            try:
                brief = post.content_text or ""
                if not brief or len(brief) >= BRIEF_THRESHOLD:
                    skipped += 1
                    continue

                # Generate caption
                caption = await _generate_caption_for_post(
                    agent=agent,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    post=post,
                    ctx=ctx,
                )
                if caption:
                    post.content_text = caption
                    generated += 1
                    logger.info(
                        "Generated caption for post %s (%s, %d chars)",
                        post.id, post.platform, len(caption),
                    )
                else:
                    logger.warning("Caption generation returned empty for post %s", post.id)
                    failed += 1
                    continue

                # Attach media if missing
                if not post.media_urls or len(post.media_urls) == 0:
                    media_urls = await _find_matching_assets(
                        db=db,
                        tenant_id=tenant_id,
                        artist_id=ctx.get("artist_id"),
                        release_id=ctx.get("release_id"),
                        campaign_id=campaign_id,
                        platform=post.platform,
                        action_type=post.action_type_label,
                        content_hint=brief,
                        day_number=post.day_number,
                    )
                    if media_urls:
                        post.media_urls = media_urls
                        assets_attached += 1

            except Exception as exc:
                logger.warning(
                    "Content generation failed for post %s: %s", post.id, exc,
                )
                failed += 1

        await db.flush()

    logger.info(
        "Content generation complete for campaign %s: %d generated, %d assets, %d failed, %d skipped",
        campaign_id, generated, assets_attached, failed, skipped,
    )

    if generated > 0:
        await _notify_content_ready(
            tenant_id, campaign_id, ctx.get("campaign_name", ""), generated,
        )

    return {
        "status": "ok",
        "pieces_generated": generated,
        "assets_attached": assets_attached,
        "failed": failed,
        "skipped": skipped,
    }


# ── Helpers ───────────────────────────────────────────────────────


async def _load_campaign_context(
    db: AsyncSession,
    campaign_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> dict | None:
    """Load campaign, artist, release, and track data for content generation."""
    from amplify.db.models.campaign import CampaignModel
    from amplify.db.models.artist import ArtistModel
    from amplify.db.models.release import ReleaseModel
    from amplify.db.models.track import TrackModel

    result = await db.execute(
        select(CampaignModel).where(
            CampaignModel.id == campaign_id,
            CampaignModel.tenant_id == tenant_id,
        )
    )
    campaign = result.scalar_one_or_none()
    if campaign is None:
        return None

    ctx: dict = {
        "campaign_name": campaign.name,
        "campaign_config": campaign.config or {},
        "artist_id": campaign.artist_id,
        "release_id": campaign.release_id,
        "artist_name": "Unknown Artist",
        "release_title": "",
        "genre": (campaign.config or {}).get("genre", ""),
        "brand_voice": "",
        "tracks": [],
    }

    if campaign.artist_id:
        artist_result = await db.execute(
            select(ArtistModel).where(ArtistModel.id == campaign.artist_id)
        )
        artist = artist_result.scalar_one_or_none()
        if artist:
            ctx["artist_name"] = artist.name
            ctx["genre"] = ctx["genre"] or getattr(artist, "genre", "") or ""
            ctx["brand_voice"] = getattr(artist, "brand_voice", "") or ""

    if campaign.release_id:
        release_result = await db.execute(
            select(ReleaseModel).where(ReleaseModel.id == campaign.release_id)
        )
        release = release_result.scalar_one_or_none()
        if release:
            ctx["release_title"] = release.title or ""

        track_result = await db.execute(
            select(TrackModel)
            .where(TrackModel.release_id == campaign.release_id)
            .order_by(TrackModel.track_number)
        )
        ctx["tracks"] = [t.title for t in track_result.scalars().all() if t.title]

    return ctx


async def _find_eligible_posts(
    db: AsyncSession,
    campaign_id: uuid.UUID,
    tenant_id: uuid.UUID,
    specific_post_ids: list[uuid.UUID] | None = None,
) -> list:
    """Find draft posts that need AI-generated content.

    Eligible posts:
    - status = 'draft'
    - content_text is short (looks like a brief, not a finished caption)
    - belong to the specified campaign
    """
    from amplify.db.models.post import PostModel

    stmt = select(PostModel).where(
        PostModel.campaign_id == campaign_id,
        PostModel.tenant_id == tenant_id,
        PostModel.status == "draft",
    )

    if specific_post_ids:
        stmt = stmt.where(PostModel.id.in_(specific_post_ids))

    result = await db.execute(stmt)
    posts = result.scalars().all()

    # Filter to posts whose content_text looks like a brief (short text)
    # rather than an already-generated caption
    eligible = [
        p for p in posts
        if not p.content_text or len(p.content_text or "") < BRIEF_THRESHOLD
    ]
    return eligible


async def _generate_caption_for_post(
    *,
    agent,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    post,
    ctx: dict,
) -> str | None:
    """Call ContentAgent to generate a caption for one post."""
    result = await agent.generate_caption(
        tenant_id=tenant_id,
        user_id=user_id,
        artist_name=ctx["artist_name"],
        platform=post.platform or "instagram",
        content_type="caption",
        release_title=ctx["release_title"],
        track_title=post.track_reference or "",
        key_message=post.content_text or "",
        cta_destination=post.destination_url or "link in bio",
        tone="authentic",
        brand_voice=ctx.get("brand_voice", ""),
    )

    # Pick the best variant from structured output
    if result.structured and hasattr(result.structured, "variants"):
        variants = result.structured.variants
        if variants:
            best = variants[0]
            caption = best.resolved_body
            if best.hashtags:
                if isinstance(best.hashtags, list):
                    caption += "\n\n" + " ".join(best.hashtags)
                elif isinstance(best.hashtags, str) and best.hashtags:
                    caption += "\n\n" + best.hashtags
            return caption

    # Fallback: parse raw text as JSON
    if result.text:
        raw = result.text.strip()
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
        if fence_match:
            raw = fence_match.group(1).strip()

        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                variants = parsed.get("variants", [])
                if variants and isinstance(variants, list):
                    v = variants[0]
                    body = (
                        v.get("copy") or v.get("body") or v.get("text")
                        or v.get("caption") or ""
                    )
                    if body:
                        hashtags = v.get("hashtags", "")
                        if isinstance(hashtags, list):
                            hashtags = " ".join(hashtags)
                        if hashtags and hashtags not in body:
                            body += "\n\n" + hashtags
                        return body
        except (ValueError, KeyError, TypeError):
            pass

        # Plain text (not JSON) — use as-is
        if not raw.startswith("{") and not raw.startswith("["):
            return raw

    return None


async def _find_matching_assets(
    *,
    db: AsyncSession,
    tenant_id: uuid.UUID,
    artist_id: uuid.UUID | None,
    release_id: uuid.UUID | None,
    campaign_id: uuid.UUID | None,
    platform: str | None,
    action_type: str | None,
    content_hint: str = "",
    day_number: int | None = None,
) -> list[str]:
    """Find matching visual assets from the library for a post.

    Simplified version of the API's content_pipeline asset matcher.
    Searches by campaign → release → artist → tenant, returning the
    best-matching visual asset URL.
    """
    from amplify.db.models.asset import AssetModel
    from sqlalchemy import or_

    VISUAL_TYPES = {"image", "album_art", "promo_photo", "video", "lyric_video", "logo"}

    # Progressive broadening: campaign → release → artist → any
    for filters in [
        {"campaign_id": campaign_id} if campaign_id else None,
        {"release_id": release_id} if release_id else None,
        {"artist_id": artist_id} if artist_id else None,
        {},
    ]:
        if filters is None:
            continue

        q = select(AssetModel).where(
            AssetModel.tenant_id == tenant_id,
            or_(
                AssetModel.approval_status != "rejected",
                AssetModel.approval_status.is_(None),
            ),
        )
        for col, val in filters.items():
            q = q.where(getattr(AssetModel, col) == val)
        q = q.order_by(AssetModel.created_at.desc()).limit(20)

        result = await db.execute(q)
        candidates = [
            a for a in result.scalars().all()
            if a.asset_type in VISUAL_TYPES and a.file_url
        ]
        if candidates:
            # Simple scoring: prefer assets whose name matches content hint
            hint_lower = content_hint.lower() if content_hint else ""
            scored = []
            for asset in candidates:
                score = 0
                name_lower = (asset.name or "").lower()
                if hint_lower and name_lower and name_lower in hint_lower:
                    score += 50
                if release_id and asset.release_id == release_id:
                    score += 10
                if campaign_id and asset.campaign_id == campaign_id:
                    score += 8
                scored.append((score, asset))

            scored.sort(key=lambda x: x[0], reverse=True)

            # Rotate among top candidates for variety
            if len(scored) > 1 and day_number is not None:
                top_score = scored[0][0]
                close = [a for s, a in scored if s >= top_score - 20]
                pick = close[day_number % len(close)] if close else scored[0][1]
            else:
                pick = scored[0][1]

            return [pick.file_url]

    return []


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
        logger.warning("Failed to send content-ready notification for campaign %s", campaign_id)

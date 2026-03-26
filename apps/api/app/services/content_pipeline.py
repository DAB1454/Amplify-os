"""Content generation pipeline — triggered on post approval.

When a post is approved, this service:
1. Generates a real caption from the brief using the ContentAgent
2. Finds matching assets from the asset library
3. Attaches the best media to the post
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def generate_content_for_post(
    db: AsyncSession,
    post_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> dict:
    """Generate real content for an approved post.

    Returns a dict with what was updated.
    """
    from amplify.db.models.post import PostModel
    from amplify.db.models.campaign import CampaignModel
    from amplify.db.models.artist import ArtistModel
    from amplify.db.models.release import ReleaseModel
    from amplify.db.models.asset import AssetModel

    # Load the post
    result = await db.execute(
        select(PostModel).where(PostModel.id == post_id, PostModel.tenant_id == tenant_id)
    )
    post = result.scalar_one_or_none()
    if not post:
        logger.warning("Content pipeline: post %s not found", post_id)
        return {"error": "post_not_found"}

    # Load campaign context
    artist_name = "Unknown Artist"
    release_title = ""
    release_id = None
    artist_id = None

    if post.campaign_id:
        camp_result = await db.execute(
            select(CampaignModel).where(CampaignModel.id == post.campaign_id)
        )
        campaign = camp_result.scalar_one_or_none()
        if campaign:
            artist_id = campaign.artist_id
            release_id = campaign.release_id

            # Get artist name
            if campaign.artist_id:
                artist_result = await db.execute(
                    select(ArtistModel).where(ArtistModel.id == campaign.artist_id)
                )
                artist = artist_result.scalar_one_or_none()
                if artist:
                    artist_name = artist.name

            # Get release title
            if campaign.release_id:
                release_result = await db.execute(
                    select(ReleaseModel).where(ReleaseModel.id == campaign.release_id)
                )
                release = release_result.scalar_one_or_none()
                if release:
                    release_title = release.title or ""

    updates: dict = {}

    # Step 1: Generate real caption from the brief
    brief = post.content_text or ""
    if brief and len(brief) < 500:  # Looks like a brief, not a real caption
        try:
            caption = await _generate_caption(
                tenant_id=tenant_id,
                user_id=user_id,
                artist_name=artist_name,
                release_title=release_title,
                platform=post.platform or "instagram",
                brief=brief,
            )
            if caption:
                post.content_text = caption
                updates["caption_generated"] = True
                logger.info("Generated caption for post %s (%d chars)", post_id, len(caption))
        except Exception as exc:
            logger.warning("Caption generation failed for post %s: %s", post_id, exc)
            updates["caption_error"] = str(exc)

    # Step 2: Find and attach matching assets from the library
    if not post.media_urls or len(post.media_urls) == 0:
        try:
            media_urls = await _find_matching_assets(
                db=db,
                tenant_id=tenant_id,
                artist_id=artist_id,
                release_id=release_id,
                campaign_id=post.campaign_id,
                platform=post.platform,
                action_type=post.action_type_label,
                content_hint=brief,
                day_number=post.day_number,
            )
            if media_urls:
                post.media_urls = media_urls
                updates["assets_attached"] = len(media_urls)
                logger.info("Attached %d assets to post %s", len(media_urls), post_id)
        except Exception as exc:
            logger.warning("Asset matching failed for post %s: %s", post_id, exc)
            updates["asset_error"] = str(exc)

    await db.flush()
    return updates


async def _generate_caption(
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    artist_name: str,
    release_title: str,
    platform: str,
    brief: str,
) -> str | None:
    """Use the ContentAgent to turn a brief into a real caption."""
    from amplify.agents.runtime.agent_runner import AgentRunner
    from amplify.agents.runtime.config import AgentConfig
    from amplify.agents.subagents.content_agent import ContentAgent

    config = AgentConfig()
    runner = AgentRunner(config=config)
    agent = ContentAgent(runner)

    result = await agent.generate_caption(
        tenant_id=tenant_id,
        user_id=user_id,
        artist_name=artist_name,
        platform=platform,
        content_type="caption",
        release_title=release_title,
        key_message=brief,
        cta_destination="link in bio",
        tone="authentic",
    )

    # Pick the best variant
    if result.structured and hasattr(result.structured, "variants"):
        variants = result.structured.variants
        if variants:
            best = variants[0]
            caption = best.body
            if best.hashtags:
                caption += "\n\n" + " ".join(best.hashtags)
            return caption

    # Fallback to raw text
    if result.text:
        return result.text

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
    """Find the best matching asset from the library.

    Uses content_hint (the post brief) to match assets by name/tags,
    and day_number to rotate through assets for variety.
    """
    from amplify.db.models.asset import AssetModel

    desired_types = _desired_asset_types(action_type)

    # Load all candidate assets (broadening scope as needed)
    candidates: list = []
    for filters in [
        {"campaign_id": campaign_id} if campaign_id else None,
        {"release_id": release_id} if release_id else None,
        {"artist_id": artist_id} if artist_id else None,
        {},
    ]:
        if filters is None:
            continue

        q = select(AssetModel).where(AssetModel.tenant_id == tenant_id)
        for col, val in filters.items():
            q = q.where(getattr(AssetModel, col) == val)
        if desired_types:
            q = q.where(AssetModel.asset_type.in_(desired_types))
        q = q.order_by(AssetModel.created_at.desc()).limit(20)

        result = await db.execute(q)
        candidates = list(result.scalars().all())
        if candidates:
            break

    if not candidates:
        return []

    # Score candidates based on content hint matching
    hint_lower = content_hint.lower()
    hint_words = set(hint_lower.split())

    scored = []
    for asset in candidates:
        score = 0
        name_lower = (asset.name or "").lower()
        desc_lower = (asset.description or "").lower()
        tag_text = " ".join(asset.tags or []).lower()

        # Name match (strongest signal)
        for word in hint_words:
            if len(word) > 3:  # skip short words
                if word in name_lower:
                    score += 10
                if word in desc_lower:
                    score += 5
                if word in tag_text:
                    score += 5

        # Prefer images for posts, videos for reels/stories
        if action_type and action_type.lower() in ("reel", "short", "story"):
            if asset.asset_type == "video":
                score += 3
        else:
            if asset.asset_type in ("image", "album_art", "promo_photo"):
                score += 3

        scored.append((score, asset))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    # Use day_number to rotate through top candidates for variety
    if len(scored) > 1 and day_number:
        idx = (day_number - 1) % min(len(scored), 5)
        # If top scores are tied, pick by rotation
        top_score = scored[0][0]
        top_tier = [s for s in scored if s[0] >= top_score - 2]
        if len(top_tier) > 1:
            idx = (day_number - 1) % len(top_tier)
            return [top_tier[idx][1].file_url]

    return [scored[0][1].file_url]


def _desired_asset_types(action_type: str | None) -> list[str]:
    """Map post action types to preferred asset types."""
    if not action_type:
        return ["image", "video", "promo_photo", "album_art"]

    action = action_type.lower()
    if action in ("reel", "short", "story"):
        return ["video"]
    if action in ("post", "engagement"):
        return ["image", "promo_photo", "album_art", "video"]
    if action == "live":
        return []  # No pre-made assets for live
    return ["image", "video", "promo_photo", "album_art"]


async def generate_content_for_posts(
    db: AsyncSession,
    post_ids: list[uuid.UUID],
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> dict:
    """Generate content for multiple posts (used by approve-all)."""
    results = {}
    for post_id in post_ids:
        try:
            result = await generate_content_for_post(db, post_id, tenant_id, user_id)
            results[str(post_id)] = result
        except Exception as exc:
            logger.warning("Content pipeline failed for post %s: %s", post_id, exc)
            results[str(post_id)] = {"error": str(exc)}
    return results

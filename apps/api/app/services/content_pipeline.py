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
            max_media = _desired_media_count(post.platform, post.action_type_label)
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
                max_results=max_media,
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

    # Fallback: try to parse raw text as JSON and extract caption
    if result.text:
        import json as _json
        import re as _re

        raw = result.text.strip()

        # Strip markdown code fences
        fence_match = _re.search(r"```(?:json)?\s*\n?(.*?)```", raw, _re.DOTALL)
        if fence_match:
            raw = fence_match.group(1).strip()

        # Try to extract structured caption from JSON
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                variants = parsed.get("variants", [])
                if variants and isinstance(variants, list):
                    v = variants[0]
                    # Try common field names for the caption text
                    body = v.get("copy") or v.get("body") or v.get("text") or v.get("caption") or ""
                    if body:
                        hashtags = v.get("hashtags", "")
                        if isinstance(hashtags, list):
                            hashtags = " ".join(hashtags)
                        # Don't append if hashtags are already in the body
                        if hashtags and hashtags not in body:
                            body += "\n\n" + hashtags
                        return body
        except (ValueError, KeyError, TypeError):
            pass

        # If it doesn't look like JSON, return as-is (it's a real caption)
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
    assets_required: list[str] | None = None,
    max_results: int = 1,
) -> list[str]:
    """Find matching assets from the library.

    Improved matching:
    - Uses assets_required hints from the planner (e.g. "album art", "promo photo")
    - Scores by content hint keyword overlap with asset name/tags/description
    - Rotates through top candidates using day_number for variety
    - Returns multiple URLs for carousel posts
    """
    from amplify.db.models.asset import AssetModel
    from sqlalchemy import or_

    # Load all candidate assets, broadening scope progressively
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
        # Exclude rejected assets
        q = q.where(or_(AssetModel.approval_status != "rejected", AssetModel.approval_status.is_(None)))
        for col, val in filters.items():
            q = q.where(getattr(AssetModel, col) == val)
        q = q.order_by(AssetModel.created_at.desc()).limit(50)

        result = await db.execute(q)
        candidates = list(result.scalars().all())
        if candidates:
            break

    if not candidates:
        return []

    # Build combined hint text from content_hint + assets_required
    hint_lower = content_hint.lower()
    hint_words = set(w for w in hint_lower.split() if len(w) > 3)
    if assets_required:
        for req in assets_required:
            hint_words.update(w.lower() for w in req.split() if len(w) > 3)

    # Determine what we're looking for
    wants_video = action_type and action_type.lower() in ("reel", "short", "story")
    wants_audio = any(
        kw in hint_lower
        for kw in ("audio", "snippet", "listen", "hear", "music", "track", "song")
    )
    wants_album_art = any(
        kw in hint_lower
        for kw in ("album", "cover", "artwork", "album_art")
    )

    # Check assets_required for type hints
    if assets_required:
        req_text = " ".join(assets_required).lower()
        if "video" in req_text:
            wants_video = True
        if "audio" in req_text or "snippet" in req_text or "track" in req_text:
            wants_audio = True
        if "album" in req_text or "cover" in req_text or "artwork" in req_text:
            wants_album_art = True

    scored = []
    for asset in candidates:
        score = 0
        name_lower = (asset.name or "").lower()
        desc_lower = (asset.description or "").lower()
        tag_text = " ".join(asset.tags or []).lower()
        asset_text = f"{name_lower} {desc_lower} {tag_text}"

        # Keyword matching from hint + assets_required
        for word in hint_words:
            if word in name_lower:
                score += 10
            if word in desc_lower:
                score += 5
            if word in tag_text:
                score += 5

        # Type affinity scoring
        if wants_video and asset.asset_type in ("video", "lyric_video"):
            score += 15
        elif wants_audio and asset.asset_type == "audio":
            score += 15
        elif wants_album_art and asset.asset_type in ("album_art", "image"):
            score += 12
        elif not wants_video and not wants_audio:
            # Generic post — prefer visual assets
            if asset.asset_type in ("image", "album_art", "promo_photo"):
                score += 5
            elif asset.asset_type == "video":
                score += 3

        # Bonus for assets linked to the same release/campaign
        if release_id and asset.release_id == release_id:
            score += 8
        if campaign_id and asset.campaign_id == campaign_id:
            score += 6

        scored.append((score, asset))

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        return []

    # Sort by score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    # If assets_required asks for multiple types (e.g. ["audio snippet", "album art"]),
    # try to return one of each type
    if wants_audio and not wants_video:
        # Find best audio + best image to pair together
        best_audio = next((a for _, a in scored if a.asset_type == "audio"), None)
        best_image = next(
            (a for _, a in scored if a.asset_type in ("image", "album_art", "promo_photo")),
            None,
        )
        if best_audio and best_image:
            return [best_image.file_url, best_audio.file_url]
        if best_audio:
            # Audio alone — still try to pair with any image
            if best_image:
                return [best_image.file_url, best_audio.file_url]
            return [best_audio.file_url]

    # For carousel posts (multiple images), return top N unique visual assets
    if max_results > 1:
        urls = []
        seen = set()
        for _, asset in scored:
            if asset.file_url not in seen and asset.asset_type in ("image", "album_art", "promo_photo"):
                urls.append(asset.file_url)
                seen.add(asset.file_url)
            if len(urls) >= max_results:
                break
        # Fall back to any type if not enough images
        if len(urls) < max_results:
            for _, asset in scored:
                if asset.file_url not in seen:
                    urls.append(asset.file_url)
                    seen.add(asset.file_url)
                if len(urls) >= max_results:
                    break
        return urls

    # Use day_number to rotate through top candidates for variety
    if len(scored) > 1 and day_number:
        top_score = scored[0][0]
        top_tier = [s for s in scored if s[0] >= max(top_score - 5, 0)]
        if len(top_tier) > 1:
            idx = (day_number - 1) % len(top_tier)
            return [top_tier[idx][1].file_url]

    return [scored[0][1].file_url]


def _desired_media_count(platform: str | None, action_type: str | None) -> int:
    """How many media items should we attach?"""
    if not platform:
        return 1
    p = platform.lower()
    a = (action_type or "").lower()
    # Instagram carousel posts can have up to 10 images
    if p == "instagram" and a == "post":
        return 3  # Good carousel size
    return 1


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

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
    release_name: str | None = None,
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

    # ── Load release name so we can debias title-track matching ──
    # When album name == title track name, every caption referencing the album
    # would unfairly score the title track's assets highest.
    release_name_clean = ""
    if release_id and not release_name:
        try:
            from amplify.db.models.release import ReleaseModel
            rq = select(ReleaseModel).where(
                ReleaseModel.id == release_id,
                ReleaseModel.tenant_id == tenant_id,
            )
            rr = await db.execute(rq)
            rel = rr.scalar_one_or_none()
            if rel:
                release_name = rel.name
        except Exception:
            pass
    if release_name:
        release_name_clean = _normalize_for_match(release_name.lower())

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
    wants_album_art = any(
        kw in hint_lower
        for kw in ("album", "cover", "artwork", "album_art")
    )
    if assets_required:
        req_text = " ".join(assets_required).lower()
        if "video" in req_text:
            wants_video = True
        if "album" in req_text or "cover" in req_text or "artwork" in req_text:
            wants_album_art = True

    # IMPORTANT: Only consider visual assets (image, video, album_art, promo_photo, lyric_video)
    # Raw audio files should NOT be attached to social media posts —
    # they're full-length songs, not clips, and platforms don't support image+audio.
    # Audio is only useful inside generated lyric videos.
    VISUAL_TYPES = {"image", "album_art", "promo_photo", "video", "lyric_video", "logo"}
    visual_candidates = [c for c in candidates if c.asset_type in VISUAL_TYPES]

    # Fall back to all candidates only if there are zero visual assets
    pool = visual_candidates if visual_candidates else candidates

    scored = []
    for asset in pool:
        score = 0
        # Use asset name, falling back to original filename from URL if name is a UUID
        raw_name = asset.name or ""
        # If name looks like a UUID (e.g. "23efe365-7328-4635-96c3-b36fb1ac0956.wav"),
        # try to extract original filename from description or tags instead
        import re as _re
        if _re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-", raw_name):
            # Name is a UUID — use description or tags as searchable text
            raw_name = asset.description or ""
        name_lower = raw_name.lower()
        desc_lower = (asset.description or "").lower()
        tag_text = " ".join(asset.tags or []).lower()
        asset_text = f"{name_lower} {desc_lower} {tag_text}"

        logger.debug("Matching asset %s (name=%s, type=%s) against hint: %s",
                      asset.id, asset.name, asset.asset_type, hint_lower[:60])

        # ── Strong match: asset name appears in the caption (or vice versa) ──
        # This catches "Whis(key) to My Heart" in caption matching an asset
        # named "Whiskey to My Heart" or "Whis(key) to My Heart.wav"
        name_clean = _normalize_for_match(name_lower)
        hint_clean = _normalize_for_match(hint_lower)

        # Debias: if asset name matches the release/album name, the match
        # is likely about the album, NOT specifically about that track.
        # e.g. album "For Love of Country" + track "For Love of Country" —
        # every caption mentions the album name, inflating the title track.
        is_title_track_match = (
            release_name_clean
            and name_clean
            and (name_clean == release_name_clean
                 or name_clean in release_name_clean
                 or release_name_clean in name_clean)
        )

        # Check if the asset name (minus extension/type suffixes) appears in caption
        if name_clean and len(name_clean) > 3 and name_clean in hint_clean:
            if is_title_track_match:
                # Only give a small bump — might genuinely be about the title track,
                # but more likely it's just the album name in the caption
                score += 10
            else:
                score += 50  # Strong signal — the post IS about this track/asset
        # Check reverse: caption words forming a track name that appear in asset name
        elif hint_clean and len(hint_clean) > 3 and _any_phrase_match(hint_lower, name_lower):
            if is_title_track_match:
                score += 8
            else:
                score += 40

        # ── Keyword matching from hint + assets_required ──
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
        elif wants_album_art and asset.asset_type in ("album_art", "image"):
            score += 12
        else:
            # Generic post — prefer images
            if asset.asset_type in ("image", "album_art", "promo_photo"):
                score += 5
            elif asset.asset_type in ("video", "lyric_video"):
                score += 3

        # Bonus for assets linked to the same release/campaign
        if release_id and asset.release_id == release_id:
            score += 8
        if campaign_id and asset.campaign_id == campaign_id:
            score += 6

        scored.append((score, asset))

    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        return []

    # For carousel posts (multiple images), return top N unique visual assets
    if max_results > 1:
        urls = []
        seen = set()
        for _, asset in scored:
            if asset.file_url not in seen:
                urls.append(asset.file_url)
                seen.add(asset.file_url)
            if len(urls) >= max_results:
                break
        return urls

    # If there's a clear winner (>10pt gap), use it.
    # Otherwise, rotate among top candidates using day_number for variety.
    top_score = scored[0][0]
    close_candidates = [a for s, a in scored if s >= top_score - 10]
    if len(close_candidates) > 1 and day_number is not None:
        pick = close_candidates[day_number % len(close_candidates)]
        return [pick.file_url]
    return [scored[0][1].file_url]


def _normalize_for_match(text: str) -> str:
    """Strip punctuation, extensions, and common suffixes for fuzzy matching."""
    import re
    # Remove file extensions
    text = re.sub(r"\.(wav|mp3|mp4|jpeg|jpg|png|webp|flac|aac)$", "", text)
    # Remove common suffixes like "cover art", "promo", etc.
    text = re.sub(r"\s*(cover art|cover|promo|artwork|art|audio|video|clip)\s*$", "", text)
    # Normalize parentheses, punctuation
    text = text.replace("(", "").replace(")", "").replace("'", "").replace("'", "")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _any_phrase_match(caption: str, asset_name: str) -> bool:
    """Check if any meaningful phrase from the caption appears in the asset name.

    Catches cases like caption mentioning 'Boat Problems' matching asset 'Boat Problems.wav'
    """
    import re
    # Look for quoted titles or capitalized phrases in the original (pre-lowered) text
    # Since we receive lowered text, look for multi-word sequences that appear in asset name
    asset_clean = _normalize_for_match(asset_name)
    if not asset_clean or len(asset_clean) < 3:
        return False

    caption_clean = _normalize_for_match(caption)
    # Check if the full asset name appears in the caption
    if asset_clean in caption_clean:
        return True

    # Check 2-3 word sliding windows from caption against asset name
    words = caption_clean.split()
    for size in (3, 2):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i:i + size])
            if len(phrase) > 5 and phrase in asset_clean:
                return True

    return False


def _desired_media_count(platform: str | None, action_type: str | None) -> int:
    """How many media items should we attach?"""
    if not platform:
        return 1
    p = platform.lower()
    a = (action_type or "").lower().replace("_", " ")
    # Carousel posts get multiple images
    if "carousel" in a:
        return 3
    if p == "instagram" and a in ("post",):
        return 2
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

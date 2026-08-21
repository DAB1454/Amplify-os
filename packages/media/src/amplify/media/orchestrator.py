"""Post media orchestration — the generate-media cascade lifted from the API
(apps/api/app/routes/posts.py::generate_media_for_post) so the worker can run
it too. Behavior-preserving. The core takes an ALREADY-LOADED post plus plain
kwargs (no FastAPI body/Depends); the caller loads the post and commits.

`settings` is duck-typed (s3_* / aws_* / media_base_url / replicate_api_token).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _default_duration_for_action(action_type_label: str | None) -> int:
    """Pick a sensible video length based on what the post is supposed to be.

    Full-length YouTube videos were getting the 15s short treatment
    because the request default was hardcoded. Shorts/reels still want
    ~15s, but "video" and "lyric_video" need real length or they don't
    match the label on the card.
    """
    lbl = (action_type_label or "").lower().strip()
    if lbl in ("video", "long_video", "full_video", "official_video"):
        return 60
    if lbl in ("lyric_video", "lyric video"):
        return 30
    # short / reel / story / tiktok / default
    return 15




async def generate_post_media_core(
    db: "AsyncSession",
    post,
    tenant_id,
    settings,
    *,
    duration_seconds: int | None = None,
    aspect_ratio: str = "9:16",
    generate_video: bool = True,
    force_image_url: str | None = None,
    force_audio_url: str | None = None,
    generate_lyric_video: bool = False,
) -> dict:
    """Resolve media for an already-loaded post and set post.media_urls.

    Returns {"media_urls": [...], "video_generated": bool}. The caller is
    responsible for committing the session.
    """
    import time
    from sqlalchemy import select
    from amplify.db.models.campaign import CampaignModel

    post_id = post.id
    start_time = time.time()

    # Resolve artist/release from campaign
    artist_id = None
    release_id = None
    if post.campaign_id:
        camp_result = await db.execute(
            select(CampaignModel).where(CampaignModel.id == post.campaign_id)
        )
        campaign = camp_result.scalar_one_or_none()
        if campaign:
            artist_id = campaign.artist_id
            release_id = campaign.release_id

    # Step 1: Resolve image — user override, existing post media, or auto-match from library
    action_lower = (post.action_type_label or "").lower()
    content_lower = (post.content_text or "").lower()

    # Resolve effective video duration: honor an explicit client value, or
    # derive from the post's action_type_label so a "video" post doesn't
    # silently become a 15-second short.
    effective_duration = (
        duration_seconds
        if duration_seconds is not None
        else _default_duration_for_action(post.action_type_label)
    )

    is_carousel = (
        "carousel" in action_lower
        or (post.platform == "instagram"
            and action_lower not in ("reel", "reels", "short", "story")
            and any(kw in content_lower for kw in [
                "artwork", "each song", "each track", "which one", "some of",
                "sneak peek", "behind the scenes", "swipe", "slide",
            ]))
    )

    # Extract image and audio from post's existing media_urls. The previous
    # implementation did url.lower().endswith(ext), which silently dropped
    # any URL with a query string (signed URLs) or without an extension, then
    # the audio matcher fell back to caption-based scoring and picked the
    # wrong track. We strip query/fragment first, then fall back to the
    # assets table to classify any URL we still can't identify by extension.
    image_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
    audio_exts = (".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a")

    def _ext_of(url: str) -> str:
        base = url.split("?", 1)[0].split("#", 1)[0].lower()
        dot = base.rfind(".")
        return base[dot:] if dot >= 0 else ""

    existing_media = post.media_urls or []
    existing_images: list[str] = []
    existing_audio: list[str] = []
    unclassified: list[str] = []
    for u in existing_media:
        ext = _ext_of(u)
        if ext in image_exts:
            existing_images.append(u)
        elif ext in audio_exts:
            existing_audio.append(u)
        else:
            unclassified.append(u)

    if unclassified:
        from amplify.db.models.asset import AssetModel
        ar = await db.execute(
            select(AssetModel.file_url, AssetModel.asset_type).where(
                AssetModel.tenant_id == tenant_id,
                AssetModel.file_url.in_(unclassified),
            )
        )
        type_by_url = {row.file_url: (row.asset_type or "").lower() for row in ar}
        for u in unclassified:
            atype = type_by_url.get(u, "")
            if atype == "audio":
                existing_audio.append(u)
            elif atype in ("image", "album_art", "promo_photo", "logo"):
                existing_images.append(u)
            elif atype in ("video", "lyric_video", "ai_video"):
                # video URLs aren't a force_image source — leave them alone
                pass

    logger.info(
        "Post %s media classification: images=%d audio=%d unknown=%d (raw=%d)",
        post_id, len(existing_images), len(existing_audio),
        len(existing_media) - len(existing_images) - len(existing_audio),
        len(existing_media),
    )

    if force_image_url:
        image_urls = [force_image_url]
    elif existing_images:
        image_urls = existing_images
    else:
        max_images = 5 if is_carousel else 1

        from amplify.agents.pipeline.content import _find_matching_assets
        image_urls = await _find_matching_assets(
            db=db,
            tenant_id=tenant_id,
            artist_id=artist_id,
            release_id=release_id,
            campaign_id=post.campaign_id,
            platform=post.platform,
            action_type=post.action_type_label,
            content_hint=post.content_text or "",
            track_reference=post.track_reference,
            day_number=post.day_number,
            max_results=max_images,
        )

    # Use existing audio from post if no force override provided.
    # Order: explicit body override > audio in media_urls >
    # engagement.source_audio_url (preserved across lyric video renders).
    if not force_audio_url and existing_audio:
        force_audio_url = existing_audio[0]
    if not force_audio_url:
        eng = post.engagement or {}
        if isinstance(eng, dict):
            stash = eng.get("source_audio_url")
            if isinstance(stash, str) and stash:
                force_audio_url = stash
                logger.info(
                    "Post %s: recovered source_audio_url from engagement for regeneration",
                    post_id,
                )

    video_generated = False

    # Step 2: Generate video for video-native platforms/formats
    is_video_post = (
        post.platform in {"tiktok", "youtube", "twitter"}
        or (post.platform == "instagram" and action_lower in ("reel", "reels", "short", "story"))
    )
    should_generate_video = (
        generate_video
        and image_urls
        and is_video_post
    )

    # Step 2a: Try lyric video if requested or action_type is lyric_video
    lyric_video_requested = (
        generate_lyric_video or action_lower in ("lyric_video", "lyric video")
    )
    lyric_video_succeeded = False
    if lyric_video_requested and image_urls:
        # Surface whether the track has lyrics so the warning is actionable
        # — the most common cause of lyric-video fallback is "no lyrics on
        # this track row", and that's a UX problem, not an FFmpeg bug.
        track_has_lyrics = False
        try:
            from amplify.db.models.track import TrackModel
            _trow = None
            # Prefer the hard track_id anchor — exact, no string matching.
            if post.track_id:
                _t = await db.execute(
                    select(TrackModel).where(
                        TrackModel.id == post.track_id,
                        TrackModel.tenant_id == tenant_id,
                    )
                )
                _trow = _t.scalar_one_or_none()
            # Fallback for legacy posts without track_id.
            elif post.track_reference and release_id:
                _t = await db.execute(
                    select(TrackModel).where(
                        TrackModel.release_id == release_id,
                        TrackModel.title == post.track_reference,
                    )
                )
                _trow = _t.scalar_one_or_none()
            track_has_lyrics = bool(_trow and (_trow.lyrics or "").strip())
        except Exception:
            pass
        try:
            from amplify.media.post_video import _generate_lyric_video_for_post
            video_url = await _generate_lyric_video_for_post(
                db=db,
                tenant_id=tenant_id,
                post=post,
                image_url=image_urls[0],
                force_audio_url=force_audio_url,
                artist_id=artist_id,
                release_id=release_id,
                settings=settings,
                duration=min(max(effective_duration, 10), 90),
            )
            if video_url:
                post.media_urls = [video_url]
                video_generated = True
                lyric_video_succeeded = True
        except Exception as exc:
            logger.warning(
                "Lyric video generation failed for post %s (track=%r, has_lyrics=%s): %s",
                post_id, post.track_reference, track_has_lyrics, exc,
            )
        if not lyric_video_succeeded and not track_has_lyrics:
            logger.warning(
                "Post %s requested lyric_video but track %r has no lyrics — "
                "label will be downgraded after fallback media is generated",
                post_id, post.track_reference,
            )

    # Step 2b: Try Replicate AI video if enabled and budget allows
    if not video_generated and should_generate_video and not is_carousel:
        try:
            from amplify.media.video_budget import can_generate_ai_video, record_spend
            allowed, reason = await can_generate_ai_video(db, tenant_id)
            if allowed and settings.replicate_api_token:
                from amplify.media.replicate_video import generate_ai_video_for_post
                # Resolve track info for better prompts. The hard track_id
                # anchor wins; track_reference is the legacy fallback.
                track_title = post.track_reference or ""
                artist_name_str = ""
                lyrics_str = ""
                anchored_audio_url: str | None = None
                if post.track_id:
                    from amplify.db.models.track import TrackModel
                    _at = await db.execute(
                        select(TrackModel).where(
                            TrackModel.id == post.track_id,
                            TrackModel.tenant_id == tenant_id,
                        )
                    )
                    _atrow = _at.scalar_one_or_none()
                    if _atrow:
                        track_title = _atrow.title or track_title
                        lyrics_str = _atrow.lyrics or ""
                        if _atrow.audio_url:
                            anchored_audio_url = _atrow.audio_url
                if release_id:
                    from amplify.db.models.release import ReleaseModel
                    rel_r = await db.execute(
                        select(ReleaseModel).where(ReleaseModel.id == release_id)
                    )
                    rel = rel_r.scalar_one_or_none()
                    if rel:
                        artist_name_str = getattr(rel, "artist_name", "") or ""
                # Find audio URL — anchor wins over fallback library pick.
                audio_for_replicate = force_audio_url or anchored_audio_url
                if not audio_for_replicate and existing_audio:
                    audio_for_replicate = existing_audio[0]
                if audio_for_replicate and image_urls:
                    ai_video_url, cost = await generate_ai_video_for_post(
                        image_url=image_urls[0],
                        audio_url=audio_for_replicate,
                        track_title=track_title,
                        artist_name=artist_name_str,
                        lyrics=lyrics_str,
                        tenant_id=tenant_id,
                        replicate_api_token=settings.replicate_api_token,
                        s3_bucket=settings.s3_bucket,
                        s3_region=settings.s3_region,
                        aws_access_key_id=settings.aws_access_key_id,
                        aws_secret_access_key=settings.aws_secret_access_key,
                        media_base_url=getattr(settings, "media_base_url", ""),
                    )
                    if ai_video_url:
                        post.media_urls = [ai_video_url]
                        video_generated = True
                        await record_spend(db, tenant_id, cost)
                        logger.info("Replicate AI video for post %s: $%.2f", post_id, cost)
        except Exception as exc:
            logger.warning("Replicate AI video failed for post %s (falling back to static): %s", post_id, exc)

    # Step 2c: Standard video (image + audio clip)
    # For carousels, generate one video per image with varied tracks
    if not video_generated and should_generate_video:
        try:
            from amplify.media.post_video import _auto_generate_post_video
            # Build content hint with track_reference as the anchor
            video_content_hint = post.content_text or ""
            if post.track_reference:
                video_content_hint = f"🎵 Featuring: {post.track_reference}\n{video_content_hint}"

            if is_carousel and len(image_urls) > 1:
                # Carousel: generate a video per image. With a track anchor
                # set, every slide uses the same track (intentional — the
                # post is about one track). Without an anchor, rotate
                # through tracks so each slide features a different song.
                video_urls = []
                for img_idx, img_url in enumerate(image_urls):
                    try:
                        vid_url = await _auto_generate_post_video(
                            db=db,
                            tenant_id=tenant_id,
                            image_url=img_url,
                            artist_id=artist_id,
                            release_id=release_id,
                            content_hint=video_content_hint,
                            day_number=(post.day_number or 1) + img_idx,
                            settings=settings,
                            duration=min(max(effective_duration, 10), 180),
                            force_audio_url=force_audio_url,
                            carousel_index=None if post.track_id else img_idx,
                            track_id=post.track_id,
                        )
                        if vid_url:
                            video_urls.append(vid_url)
                    except Exception as exc:
                        logger.warning("Carousel video %d failed for post %s: %s", img_idx, post_id, exc)
                        video_urls.append(img_url)  # fallback to image
                if video_urls:
                    post.media_urls = video_urls
                    video_generated = True
            else:
                video_url = await _auto_generate_post_video(
                    db=db,
                    tenant_id=tenant_id,
                    image_url=image_urls[0],
                    artist_id=artist_id,
                    release_id=release_id,
                    content_hint=video_content_hint,
                    day_number=post.day_number or 1,
                    settings=settings,
                    duration=min(max(effective_duration, 10), 180),
                    force_audio_url=force_audio_url,
                    track_id=post.track_id,
                )
                if video_url:
                    post.media_urls = [video_url]
                    video_generated = True
        except Exception as exc:
            logger.warning("Video generation failed for post %s (falling back to image): %s", post_id, exc)

    # If no video was generated, attach the image(s) directly
    if not video_generated and image_urls:
        post.media_urls = image_urls

    # If still no media found at all, return empty (don't error — library may be empty)
    if not post.media_urls:
        post.media_urls = []
        logger.info("No assets found for post %s — media_urls stays empty", post_id)

    # Truth-in-labeling: if lyric video was requested but the cascade fell
    # through to a regular short / clip, downgrade the label so the UI
    # doesn't lie about what the post actually is. Per-platform default:
    # YouTube → "short", IG/TikTok → "reel".
    if lyric_video_requested and not lyric_video_succeeded and video_generated:
        plat = (post.platform or "").lower()
        downgraded = "short" if plat == "youtube" else "reel"
        old_label = post.action_type_label
        post.action_type_label = downgraded
        logger.info(
            "Post %s label downgraded %r → %r (lyric video fallback)",
            post_id, old_label, downgraded,
        )

    await db.flush()

    elapsed = int((time.time() - start_time) * 1000)
    logger.info("Media generated for post %s in %dms (video=%s, urls=%d)",
                post_id, elapsed, video_generated, len(post.media_urls or []))

    return {
        "media_urls": list(post.media_urls or []),
        "video_generated": video_generated,
    }

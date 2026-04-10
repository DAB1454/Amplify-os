"""Post CRUD and publishing workflow routes."""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_settings, get_tenant_id, get_user_id, get_audit_service
from app.config import Settings
from pydantic import BaseModel, Field
from app.schemas import (
    PostCreateRequest,
    PostUpdateRequest,
    PostResponse,
    PostQueueResponse,
    PostActionResponse,
    PostStatusResponse,
    ScheduleRequest,
)
from app.services.audit_service import AuditService
from app.services.learning_event_service import LearningEventService
from app.services.publishing_service import PublishingService, InvalidTransition
from amplify.db.models.post import PostModel
from amplify.db.repository import BaseRepository

router = APIRouter(prefix="/posts", tags=["posts"])


# ── Dependencies ─────────────────────────────────────────────────


def _get_learning_service(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> LearningEventService:
    return LearningEventService(db, tenant_id)


def _get_publishing_service(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    learning: LearningEventService = Depends(_get_learning_service),
) -> PublishingService:
    return PublishingService(db, tenant_id, learning=learning)


# ── CRUD ─────────────────────────────────────────────────────────


@router.get("", response_model=list[PostResponse])
@router.get("/", response_model=list[PostResponse])
async def list_posts(
    campaign_id: uuid.UUID | None = Query(default=None),
    platform: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """List posts, optionally filtered by campaign_id, platform, and/or status."""
    repo = BaseRepository(db, PostModel, tenant_id)
    filters = {}
    if campaign_id is not None:
        filters["campaign_id"] = campaign_id
    if platform is not None:
        filters["platform"] = platform
    if status_filter is not None:
        filters["status"] = status_filter
    return await repo.list(**filters)


@router.post("", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
async def create_post(
    body: PostCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
    learning: LearningEventService = Depends(_get_learning_service),
):
    """Create a new post."""
    import logging
    from datetime import timezone
    logger = logging.getLogger(__name__)
    try:
        data = body.model_dump()
        # Strip timezone info — DB stores naive UTC datetimes
        for dt_field in ("scheduled_at", "published_at"):
            if dt_field in data and data[dt_field] is not None:
                dt_val = data[dt_field]
                if hasattr(dt_val, "tzinfo") and dt_val.tzinfo is not None:
                    data[dt_field] = dt_val.astimezone(timezone.utc).replace(tzinfo=None)
        repo = BaseRepository(db, PostModel, tenant_id)
        entity = await repo.create(**data)
    except Exception as exc:
        logger.error("Post create failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Post creation failed: {exc}")

    try:
        await audit.log(
            action="post.created",
            entity_type="post",
            entity_id=entity.id,
            user_id=user_id,
            changes=body.model_dump(mode="json"),
        )
    except Exception as exc:
        logger.warning("Audit log failed: %s", exc)

    # Learning event — fire-and-forget, must not break the main transaction
    try:
        from amplify.learning.capture import post_created
        event_data = post_created(
            post_id=entity.id,
            tenant_id=tenant_id,
            platform=entity.platform,
            campaign_id=entity.campaign_id,
            content_type=None,
            content_length=len(entity.content_text or ""),
            has_media=bool(entity.media_urls),
        )
        await learning.emit(**{
            k: v for k, v in event_data.items() if k != "tenant_id"
        })
    except Exception as exc:
        logger.warning("Learning event failed (non-fatal): %s", exc)

    return entity


@router.get("/{post_id}", response_model=PostResponse)
async def get_post(
    post_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Get a post by ID."""
    repo = BaseRepository(db, PostModel, tenant_id)
    entity = await repo.get(post_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return entity


@router.put("/{post_id}", response_model=PostResponse)
async def update_post(
    post_id: uuid.UUID,
    body: PostUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Update a post by ID."""
    from datetime import timezone

    repo = BaseRepository(db, PostModel, tenant_id)
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Strip timezone info — DB stores naive UTC datetimes
    for dt_field in ("scheduled_at", "published_at"):
        if dt_field in updates and updates[dt_field] is not None:
            dt_val = updates[dt_field]
            if hasattr(dt_val, "tzinfo") and dt_val.tzinfo is not None:
                updates[dt_field] = dt_val.astimezone(timezone.utc).replace(tzinfo=None)

    entity = await repo.update(post_id, **updates)
    if entity is None:
        raise HTTPException(status_code=404, detail="Post not found")

    try:
        await audit.log(
            action="post.updated",
            entity_type="post",
            entity_id=entity.id,
            user_id=user_id,
            changes=updates,
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return entity


@router.delete("/{post_id}")
async def delete_post(
    post_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Delete a post — attempts platform removal, then deletes from DB.

    Returns JSON with deletion status so the frontend can inform the user.
    """
    from sqlalchemy import select

    # Load the post to get platform_post_id and channel_id
    result = await db.execute(
        select(PostModel).where(PostModel.id == post_id, PostModel.tenant_id == tenant_id)
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Best-effort remote deletion if the post was published
    platform_deleted = None  # None = not attempted, True/False = result
    platform_message = None
    if post.platform_post_id and post.channel_id:
        try:
            from app.services.adapter_factory import get_adapter
            adapter = await get_adapter(db, post.channel_id, settings, require_publish=False)
            platform_deleted = await adapter.delete_post(post.platform_post_id)
            if not platform_deleted:
                platform_message = f"Post removed from AmplifyMe but must be deleted manually on {post.platform.title()}"
        except Exception as exc:
            platform_deleted = False
            platform_message = f"Could not reach {post.platform.title()} — delete manually if needed"
            logger.warning("Remote delete error for post %s: %s", post_id, exc)

    # Delete child records that reference this post (no CASCADE on FK)
    for child_table in ("learning_events", "post_feature_vectors", "post_outcomes", "approvals"):
        try:
            await db.execute(
                text(f"DELETE FROM {child_table} WHERE post_id = :pid"),
                {"pid": str(post_id)},
            )
        except Exception as exc:
            logger.warning("Cleanup %s failed (non-fatal): %s", child_table, exc)

    repo = BaseRepository(db, PostModel, tenant_id)
    await repo.delete(post_id)

    try:
        await audit.log(
            action="post.deleted",
            entity_type="post",
            entity_id=post_id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return {
        "status": "deleted",
        "platform_deleted": platform_deleted,
        "message": platform_message,
    }


@router.post("/{post_id}/review-approve", response_model=PostResponse)
async def review_approve_post(
    post_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Approve a pending_review post and trigger content generation."""
    repo = BaseRepository(db, PostModel, tenant_id)
    post = await repo.get(post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.approval_status not in ("pending_review", None):
        raise HTTPException(status_code=409, detail=f"Post approval_status is '{post.approval_status}', expected 'pending_review'")

    # Just approve — media generation is a separate step via POST /posts/{id}/generate-media
    entity = await repo.update(post_id, approval_status="approved")

    try:
        await audit.log(
            action="post.review_approved",
            entity_type="post",
            entity_id=post_id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return entity


@router.post("/{post_id}/review-reject", response_model=PostResponse)
async def review_reject_post(
    post_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Reject a pending_review post (AI plan review workflow)."""
    repo = BaseRepository(db, PostModel, tenant_id)
    post = await repo.get(post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    entity = await repo.update(post_id, approval_status="rejected")

    try:
        await audit.log(
            action="post.review_rejected",
            entity_type="post",
            entity_id=post_id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return entity


# ── Media Generation ─────────────────────────────────────────────


class GenerateMediaRequest(BaseModel):
    # None = derive from the post's action_type_label. Clients can still
    # pin an exact duration (e.g. the "Regenerate" dropdown) by passing an
    # int, but the default used to be 15 which meant every YouTube "video"
    # post came out as a 15-second clip instead of a full-length video.
    duration_seconds: int | None = None
    aspect_ratio: str = "9:16"
    generate_video: bool = True  # Auto-generate video for TikTok/YouTube posts
    force_image_url: str | None = None  # User-selected image override
    force_audio_url: str | None = None  # User-selected audio override
    generate_lyric_video: bool = False  # Generate lyric overlay video


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


class GenerateMediaResponse(BaseModel):
    media_urls: list[str] = Field(default_factory=list)
    video_generated: bool = False
    elapsed_ms: int = 0


@router.post("/{post_id}/generate-media", response_model=GenerateMediaResponse)
async def generate_media_for_post(
    post_id: uuid.UUID,
    body: GenerateMediaRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    settings_obj: Settings = Depends(get_settings),
):
    """Generate media for a single post — find assets + optionally create video.

    This is the per-post generation step in the 3-step workflow:
    1. Plan creates posts (caption + channel + date, no media)
    2. Generate media for each post (this endpoint)
    3. User reviews preview, then schedules/publishes
    """
    import time
    from sqlalchemy import select
    from amplify.db.models.campaign import CampaignModel

    start_time = time.time()

    repo = BaseRepository(db, PostModel, tenant_id)
    post = await repo.get(post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

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
        body.duration_seconds
        if body.duration_seconds is not None
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

    # Extract image and audio from post's existing media_urls
    existing_media = post.media_urls or []
    existing_images = [u for u in existing_media if any(u.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"))]
    existing_audio = [u for u in existing_media if any(u.lower().endswith(ext) for ext in (".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a"))]

    if body.force_image_url:
        image_urls = [body.force_image_url]
    elif existing_images:
        image_urls = existing_images
    else:
        max_images = 5 if is_carousel else 1

        from app.services.content_pipeline import _find_matching_assets
        # Use track_reference as the primary matching anchor when available
        content_hint = post.content_text or ""
        if post.track_reference:
            content_hint = f"🎵 Featuring: {post.track_reference}\n{content_hint}"
        image_urls = await _find_matching_assets(
            db=db,
            tenant_id=tenant_id,
            artist_id=artist_id,
            release_id=release_id,
            campaign_id=post.campaign_id,
            platform=post.platform,
            action_type=post.action_type_label,
            content_hint=content_hint,
            day_number=post.day_number,
            max_results=max_images,
        )

    # Use existing audio from post if no force override provided
    if not body.force_audio_url and existing_audio:
        body.force_audio_url = existing_audio[0]

    video_generated = False

    # Step 2: Generate video for video-native platforms/formats
    is_video_post = (
        post.platform in {"tiktok", "youtube"}
        or (post.platform == "instagram" and action_lower in ("reel", "reels", "short", "story"))
    )
    should_generate_video = (
        body.generate_video
        and image_urls
        and is_video_post
    )

    # Step 2a: Try lyric video if requested or action_type is lyric_video
    lyric_video_requested = (
        body.generate_lyric_video or action_lower in ("lyric_video", "lyric video")
    )
    lyric_video_succeeded = False
    if lyric_video_requested and image_urls:
        # Surface whether the track has lyrics so the warning is actionable
        # — the most common cause of lyric-video fallback is "no lyrics on
        # this track row", and that's a UX problem, not an FFmpeg bug.
        track_has_lyrics = False
        if post.track_reference and release_id:
            try:
                from amplify.db.models.track import TrackModel
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
            from app.routes.ai import _generate_lyric_video_for_post
            video_url = await _generate_lyric_video_for_post(
                db=db,
                tenant_id=tenant_id,
                post=post,
                image_url=image_urls[0],
                force_audio_url=body.force_audio_url,
                artist_id=artist_id,
                release_id=release_id,
                settings=settings_obj,
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
            from app.services.video_budget_service import can_generate_ai_video, record_spend
            allowed, reason = await can_generate_ai_video(db, tenant_id)
            if allowed and settings_obj.replicate_api_token:
                from app.services.replicate_video import generate_ai_video_for_post
                # Resolve track info for better prompts
                track_title = post.track_reference or ""
                artist_name_str = ""
                lyrics_str = ""
                if release_id:
                    from amplify.db.models.release import ReleaseModel
                    rel_r = await db.execute(
                        select(ReleaseModel).where(ReleaseModel.id == release_id)
                    )
                    rel = rel_r.scalar_one_or_none()
                    if rel:
                        artist_name_str = getattr(rel, "artist_name", "") or ""
                # Find audio URL
                audio_for_replicate = body.force_audio_url
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
                        replicate_api_token=settings_obj.replicate_api_token,
                        s3_bucket=settings_obj.s3_bucket,
                        s3_region=settings_obj.s3_region,
                        aws_access_key_id=settings_obj.aws_access_key_id,
                        aws_secret_access_key=settings_obj.aws_secret_access_key,
                        media_base_url=getattr(settings_obj, "media_base_url", ""),
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
            from app.routes.ai import _auto_generate_post_video
            # Build content hint with track_reference as the anchor
            video_content_hint = post.content_text or ""
            if post.track_reference:
                video_content_hint = f"🎵 Featuring: {post.track_reference}\n{video_content_hint}"

            if is_carousel and len(image_urls) > 1:
                # Carousel: generate a video per image, rotating tracks
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
                            settings=settings_obj,
                            duration=min(max(effective_duration, 10), 180),
                            force_audio_url=body.force_audio_url,
                            carousel_index=img_idx,
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
                    settings=settings_obj,
                    duration=min(max(effective_duration, 10), 180),
                    force_audio_url=body.force_audio_url,
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

    return GenerateMediaResponse(
        media_urls=list(post.media_urls or []),
        video_generated=video_generated,
        elapsed_ms=elapsed,
    )


# ── Retry / Reset ─────────────────────────────────────────────────


class RetryRequest(BaseModel):
    republish: bool = False  # If true, reset AND immediately re-publish


@router.post("/{post_id}/retry")
async def retry_stuck_post(
    post_id: uuid.UUID,
    body: RetryRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Reset a stuck 'publishing' or 'failed' post back to 'scheduled', optionally re-publish immediately."""
    repo = BaseRepository(db, PostModel, tenant_id)
    post = await repo.get(post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.status not in ("publishing", "failed", "published", "pending_approval"):
        raise HTTPException(status_code=409, detail=f"Post is '{post.status}', cannot retry")

    post.status = "scheduled"
    post.last_error = None
    post.retry_count = 0
    await db.flush()

    if body.republish:
        # Immediately attempt to publish
        try:
            svc = PublishingService(db=db, tenant_id=tenant_id)
            result = await svc.publish_post(post_id)
            return {"post_id": str(post_id), "status": result.get("status", "published"), "message": "Post re-published successfully"}
        except Exception as exc:
            logger.warning("Re-publish failed for %s: %s", post_id, exc)
            return {"post_id": str(post_id), "status": "scheduled", "message": f"Reset to scheduled, but re-publish failed: {exc}"}

    return {"post_id": str(post_id), "status": "scheduled", "message": "Post reset — will retry on next scheduler run"}


# ── Manual Publish Override ──────────────────────────────────────
#
# Rarely-used admin escape hatch for posts that are LIVE on a platform
# but whose AmplifyMe row never made it to the 'published' state — e.g.
# bookkeeping raised after the platform API already accepted the post.
# Forces the state regardless of the current status so metrics ingestion
# can find it.


class MarkPublishedRequest(BaseModel):
    platform_post_id: str | None = None
    permalink: str | None = None
    published_at: str | None = None  # ISO8601; defaults to now


@router.post("/{post_id}/mark-published")
async def mark_post_published(
    post_id: uuid.UUID,
    body: MarkPublishedRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Force a post to PUBLISHED with the given platform IDs.

    Bypasses the state machine — intended only for recovering posts
    that went live externally but weren't recorded correctly (e.g.
    after a bookkeeping crash). Emits the learning event so metrics
    ingestion picks the post up on the next sync.
    """
    from datetime import datetime
    from amplify.core.domain.post import PostStatus

    repo = BaseRepository(db, PostModel, tenant_id)
    post = await repo.get(post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    if body.published_at:
        try:
            pub_at = datetime.fromisoformat(body.published_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid published_at: {body.published_at}")
    else:
        pub_at = datetime.utcnow()

    previous_status = post.status
    post.status = PostStatus.PUBLISHED.value
    post.published_at = pub_at
    if body.platform_post_id:
        post.platform_post_id = body.platform_post_id
    if body.permalink:
        post.permalink = body.permalink
    post.last_error = None
    post.retry_count = 0
    await db.flush()

    # Emit post_published so analytics/metrics backfill can pick it up.
    try:
        from amplify.learning.capture import post_published
        svc = LearningEventService(db, tenant_id)
        await svc.emit(**{
            k: v for k, v in post_published(
                post_id=post_id,
                tenant_id=tenant_id,
                platform=post.platform or "",
                platform_post_id=post.platform_post_id,
                permalink=post.permalink,
                published_at=post.published_at,
                campaign_id=post.campaign_id,
            ).items()
            if k != "tenant_id"
        })
    except Exception as exc:
        logger.warning("mark-published learning event failed for %s: %s", post_id, exc)

    logger.info(
        "Post %s force-marked published (was %s) — platform_post_id=%s permalink=%s",
        post_id, previous_status, post.platform_post_id, post.permalink,
    )

    return {
        "post_id": str(post_id),
        "status": post.status,
        "previous_status": previous_status,
        "published_at": post.published_at.isoformat() if post.published_at else None,
        "platform_post_id": post.platform_post_id,
        "permalink": post.permalink,
    }


# ── Repurpose (cross-post to other channels) ─────────────────────


class RepurposeRequest(BaseModel):
    channel_ids: list[uuid.UUID] = Field(
        ..., min_length=1, max_length=10,
        description="Target channels to create draft copies on",
    )
    action_type_label: str | None = Field(
        None,
        description=(
            "Override format for the new posts (e.g. 'story', 'reel', 'short'). "
            "If omitted, keeps the original format."
        ),
    )


@router.post("/{post_id}/repurpose")
async def repurpose_post(
    post_id: uuid.UUID,
    body: RepurposeRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Create draft copies of an existing post on other channels.

    Copies content_text, media_urls, goal, track_reference, and campaign
    from the source post, targeting the specified channels. Each copy
    links back via repurposed_from_id so the learning layer can compare
    same-content performance across channels.

    Optionally override the action_type_label to adapt the format (e.g.
    push a Reel to an IG Story, or a YouTube Short to TikTok).
    """
    from amplify.db.models.channel import ChannelConnectionModel
    from sqlalchemy import select

    repo = BaseRepository(db, PostModel, tenant_id)
    source = await repo.get(post_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Post not found")

    # Verify all target channels belong to this tenant and are active
    ch_stmt = select(ChannelConnectionModel).where(
        ChannelConnectionModel.id.in_(body.channel_ids),
        ChannelConnectionModel.tenant_id == tenant_id,
        ChannelConnectionModel.is_active.is_(True),
    )
    channels = list((await db.execute(ch_stmt)).scalars().all())
    found_ids = {ch.id for ch in channels}
    missing = [str(cid) for cid in body.channel_ids if cid not in found_ids]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Channels not found or inactive: {', '.join(missing)}",
        )

    # Don't allow repurposing to the same channel the source is on
    if source.channel_id:
        channels = [ch for ch in channels if ch.id != source.channel_id]
    if not channels:
        raise HTTPException(
            status_code=400, detail="All target channels match the source — nothing to repurpose",
        )

    created = []
    for ch in channels:
        new_post = PostModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            campaign_id=source.campaign_id,
            channel_id=ch.id,
            platform=ch.platform,
            status="draft",
            content_text=source.content_text or "",
            media_urls=list(source.media_urls or []),
            destination_url=source.destination_url,
            action_type_label=body.action_type_label or source.action_type_label,
            goal=source.goal,
            track_reference=source.track_reference,
            day_number=source.day_number,
            repurposed_from_id=source.id,
        )
        db.add(new_post)
        created.append({
            "post_id": str(new_post.id),
            "platform": ch.platform,
            "channel_id": str(ch.id),
            "channel_name": ch.display_name or ch.platform,
            "action_type_label": new_post.action_type_label,
            "status": "draft",
        })

    await db.flush()
    logger.info(
        "Repurposed post %s to %d channels: %s",
        post_id, len(created), [c["platform"] for c in created],
    )

    return {
        "source_post_id": str(post_id),
        "created": created,
        "count": len(created),
    }


# ── Publishing Workflow ────────────────────────────────────────────


@router.post("/{post_id}/queue", response_model=PostQueueResponse)
async def queue_post(
    post_id: uuid.UUID,
    svc: PublishingService = Depends(_get_publishing_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Submit a draft post for approval (runs policy engine)."""
    try:
        result = svc.queue_post(post_id)
        if hasattr(result, "__await__"):
            result = await result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    try:
        await audit.log(
            action="post.queued",
            entity_type="post",
            entity_id=post_id,
            user_id=user_id,
            changes={"policy_decision": result.get("policy_decision")},
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)
    return result


@router.post("/{post_id}/approve", response_model=PostActionResponse)
async def approve_post(
    post_id: uuid.UUID,
    svc: PublishingService = Depends(_get_publishing_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Approve a queued post."""
    try:
        result = await svc.approve_post(post_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    try:
        await audit.log(
            action="post.approved",
            entity_type="post",
            entity_id=post_id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)
    return result


@router.post("/{post_id}/reject", response_model=PostActionResponse)
async def reject_post(
    post_id: uuid.UUID,
    svc: PublishingService = Depends(_get_publishing_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Reject a queued post back to draft."""
    try:
        result = await svc.reject_post(post_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    try:
        await audit.log(
            action="post.rejected",
            entity_type="post",
            entity_id=post_id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)
    return result


@router.post("/{post_id}/schedule", response_model=PostActionResponse)
async def schedule_post(
    post_id: uuid.UUID,
    body: ScheduleRequest,
    svc: PublishingService = Depends(_get_publishing_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Schedule a post for future publication."""
    # Strip timezone info — DB stores naive UTC datetimes
    scheduled_at = body.scheduled_at
    if scheduled_at.tzinfo is not None:
        from datetime import timezone
        scheduled_at = scheduled_at.astimezone(timezone.utc).replace(tzinfo=None)
    try:
        result = await svc.schedule_post(post_id, scheduled_at)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    try:
        await audit.log(
            action="post.scheduled",
            entity_type="post",
            entity_id=post_id,
            user_id=user_id,
            changes={"scheduled_at": scheduled_at.isoformat()},
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)
    return result


@router.post("/{post_id}/publish", response_model=PostActionResponse)
async def publish_post_now(
    post_id: uuid.UUID,
    svc: PublishingService = Depends(_get_publishing_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Publish a post immediately."""
    try:
        result = await svc.publish_post(post_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    try:
        await audit.log(
            action="post.published",
            entity_type="post",
            entity_id=post_id,
            user_id=user_id,
            changes={"status": result.get("status")},
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)
    return result


@router.post("/{post_id}/preview", response_model=PostActionResponse)
async def preview_post(
    post_id: uuid.UUID,
    svc: PublishingService = Depends(_get_publishing_service),
):
    """Dry-run preview of what would be published."""
    try:
        result = await svc.publish_post(post_id, dry_run=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return result


@router.get("/{post_id}/status", response_model=PostStatusResponse)
async def get_post_status(
    post_id: uuid.UUID,
    svc: PublishingService = Depends(_get_publishing_service),
):
    """Get detailed publishing status for a post."""
    try:
        return await svc.get_status(post_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


## Old retry endpoint removed — replaced by retry_stuck_post above (line ~442)

"""Post CRUD and publishing workflow routes."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select, text
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
    # "generating" when the work was handed to the worker (poll GET /posts/{id}
    # and read engagement.media_status); "complete" when generated inline via
    # the synchronous fallback.
    status: str = "generating"
    media_urls: list[str] = Field(default_factory=list)
    video_generated: bool = False
    elapsed_ms: int = 0


@router.post("/{post_id}/generate-media", response_model=GenerateMediaResponse)
async def generate_media_for_post(
    post_id: uuid.UUID,
    body: GenerateMediaRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    settings_obj: Settings = Depends(get_settings),
):
    """Kick off media generation for a single post.

    Enqueues the `generate_post_media` worker job and returns immediately with
    status="generating"; the frontend polls GET /posts/{id} and reads
    engagement.media_status ("generating" → "complete"/"failed"). If the queue
    is unavailable, falls back to synchronous generation so it still works.
    """
    from datetime import datetime, timezone

    repo = BaseRepository(db, PostModel, tenant_id)
    post = await repo.get(post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")

    payload = {
        "post_id": str(post_id),
        "tenant_id": str(tenant_id),
        "duration_seconds": body.duration_seconds,
        "aspect_ratio": body.aspect_ratio,
        "generate_video": body.generate_video,
        "force_image_url": body.force_image_url,
        "force_audio_url": body.force_audio_url,
        "generate_lyric_video": body.generate_lyric_video,
    }

    # Preferred path: mark generating, enqueue, return immediately.
    try:
        r = getattr(request.app.state, "redis", None)
        if r is None:
            raise RuntimeError("redis unavailable")
        from worker.app.queue import JobQueue

        eng = dict(post.engagement or {})
        eng["media_status"] = "generating"
        eng["media_status_at"] = datetime.now(timezone.utc).isoformat()
        eng.pop("media_error", None)
        post.engagement = eng
        await db.commit()

        q = JobQueue(r)
        # Media jobs can run several minutes (Replicate poll + stitch); give the
        # worker a generous ceiling well past the 300s Replicate cap.
        await q.enqueue("generate_post_media", payload, timeout_seconds=900)
        logger.info("Enqueued generate_post_media for post %s", post_id)
        return GenerateMediaResponse(status="generating")
    except Exception as exc:
        logger.warning(
            "generate-media enqueue failed for post %s (%s); running inline",
            post_id, exc,
        )

    # Synchronous fallback (queue/worker unavailable).
    import time
    from amplify.media.orchestrator import generate_post_media_core

    start_time = time.time()
    post = await repo.get(post_id)  # reload; the try-block may have committed
    result = await generate_post_media_core(
        db, post, tenant_id, settings_obj,
        duration_seconds=body.duration_seconds,
        aspect_ratio=body.aspect_ratio,
        generate_video=body.generate_video,
        force_image_url=body.force_image_url,
        force_audio_url=body.force_audio_url,
        generate_lyric_video=body.generate_lyric_video,
    )
    eng = dict(post.engagement or {})
    eng["media_status"] = "complete"
    post.engagement = eng
    await db.flush()

    elapsed = int((time.time() - start_time) * 1000)
    logger.info("Media generated inline for post %s in %dms (video=%s, urls=%d)",
                post_id, elapsed, result["video_generated"], len(result["media_urls"]))
    return GenerateMediaResponse(
        status="complete",
        media_urls=result["media_urls"],
        video_generated=result["video_generated"],
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

    if post.status not in ("publishing", "failed", "published", "pending_approval", "queued", "scheduled"):
        raise HTTPException(status_code=409, detail=f"Post is '{post.status}', cannot retry")

    post.status = "scheduled"
    post.last_error = None
    post.retry_count = 0
    post.scheduled_at = datetime.utcnow()  # ensure scan_scheduled picks it up

    # Re-stitch channel_id if it's missing (e.g. after disconnect/reconnect)
    if not post.channel_id and post.platform:
        from amplify.db.models.channel import ChannelConnectionModel
        ch_result = await db.execute(
            select(ChannelConnectionModel.id).where(
                ChannelConnectionModel.tenant_id == tenant_id,
                ChannelConnectionModel.platform == post.platform,
                ChannelConnectionModel.is_active.is_(True),
            ).limit(1)
        )
        active_ch = ch_result.scalar_one_or_none()
        if active_ch:
            post.channel_id = active_ch
            logger.info("Re-stitched channel %s to post %s", active_ch, post_id)
        else:
            return {"post_id": str(post_id), "status": "failed",
                    "message": f"No active {post.platform} channel connected. Connect it on the Channels page first."}

    await db.flush()

    if body.republish:
        # Immediately attempt to publish
        try:
            svc = PublishingService(db=db, tenant_id=tenant_id)
            result = await svc.publish_post(post_id)
            result_status = result.get("status", "published")
            if result_status == "failed":
                return {
                    "post_id": str(post_id),
                    "status": "failed",
                    "message": result.get("error", "Publish failed"),
                    "error": result.get("error"),
                }
            return {"post_id": str(post_id), "status": result_status, "message": "Post re-published successfully"}
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
            track_id=source.track_id,
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


@router.post("/{post_id}/reset-to-draft", response_model=PostActionResponse)
async def reset_to_draft_post(
    post_id: uuid.UUID,
    svc: PublishingService = Depends(_get_publishing_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Move a post back to DRAFT (replaces the old /reject and /reset_draft).

    Works from any pre-publish state: schedule, pending_approval (TikTok
    inbox), failed, or legacy queued/approved rows.
    """
    try:
        result = await svc.reset_to_draft(post_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    try:
        await audit.log(
            action="post.reset_to_draft",
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
    """Schedule a post for future publication.

    For TikTok posts, the body should include `tiktok_post_info` with
    the Content Sharing Guidelines disclosure choices captured from
    the Direct Post modal at schedule time. The worker reads these
    when the scheduled moment arrives.
    """
    # Strip timezone info — DB stores naive UTC datetimes
    scheduled_at = body.scheduled_at
    if scheduled_at.tzinfo is not None:
        from datetime import timezone
        scheduled_at = scheduled_at.astimezone(timezone.utc).replace(tzinfo=None)
    try:
        result = await svc.schedule_post(
            post_id,
            scheduled_at,
            tiktok_post_info=body.tiktok_post_info,
        )
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


class TikTokPublishParams(BaseModel):
    """Per-request TikTok disclosure choices from the Direct Post modal.

    Every field maps 1:1 to a Content Sharing Guidelines control on the
    confirm screen. The route accepts the body as optional so non-TikTok
    posts and legacy callers continue to work without change.
    """
    privacy_level: str | None = None
    disable_comment: bool | None = None
    disable_duet: bool | None = None
    disable_stitch: bool | None = None
    brand_content_toggle: bool | None = None
    brand_organic_toggle: bool | None = None
    video_cover_timestamp_ms: int | None = None


class PublishNowRequest(BaseModel):
    tiktok: TikTokPublishParams | None = None


@router.post("/{post_id}/publish", response_model=PostActionResponse)
async def publish_post_now(
    post_id: uuid.UUID,
    body: PublishNowRequest | None = None,
    svc: PublishingService = Depends(_get_publishing_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Publish a post immediately.

    For TikTok posts, the body must include `tiktok` with the user's
    Content Sharing Guidelines disclosure selections from the Direct
    Post modal. Posts published without those selections will use
    conservative defaults (private/SELF_ONLY when on an unaudited app).
    """
    platform_params: dict | None = None
    if body is not None and body.tiktok is not None:
        platform_params = body.tiktok.model_dump(exclude_none=True)

    try:
        result = await svc.publish_post(post_id, platform_params=platform_params)
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


# ── TikTok creator info (Content Sharing Guidelines) ─────────────


class TikTokCreatorInfoResponse(BaseModel):
    """Creator capabilities for the Direct Post disclosure modal.

    These fields populate every required element of TikTok's Content
    Sharing Guidelines UI (privacy options, comment/duet/stitch
    availability, max duration). All values come straight from
    TikTok's creator_info/query endpoint on the current access token.
    """
    creator_username: str
    creator_nickname: str
    creator_avatar_url: str
    privacy_level_options: list[str]
    comment_disabled: bool
    duet_disabled: bool
    stitch_disabled: bool
    max_video_post_duration_sec: int


@router.get(
    "/{post_id}/tiktok/creator-info",
    response_model=TikTokCreatorInfoResponse,
)
async def get_tiktok_creator_info(
    post_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    settings_obj: Settings = Depends(get_settings),
):
    """Return the creator's TikTok posting capabilities for the
    Direct Post disclosure modal.

    Required by TikTok's Content Sharing Guidelines: every field the
    user can see/select on the confirm screen must come from THIS
    endpoint called against the current access token immediately
    before showing the UI. Reviewers check for it.
    """
    from app.services.adapter_factory import get_adapter

    post = await db.get(PostModel, post_id)
    if post is None or post.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.platform != "tiktok":
        raise HTTPException(
            status_code=400,
            detail=f"creator-info is TikTok-only (post platform={post.platform})",
        )
    if post.channel_id is None:
        raise HTTPException(
            status_code=400,
            detail="Post is not bound to a channel — reconnect TikTok",
        )

    try:
        adapter = await get_adapter(
            db, post.channel_id, settings_obj, require_publish=True,
        )
    except Exception as exc:
        logger.warning("creator-info: adapter load failed for post %s: %s", post_id, exc)
        raise HTTPException(status_code=400, detail=f"TikTok channel not ready: {exc}")

    try:
        info = await adapter.query_creator_info()
    except Exception as exc:
        logger.warning("creator-info: TikTok API failed for post %s: %s", post_id, exc)
        raise HTTPException(status_code=502, detail=f"TikTok API error: {exc}")

    return TikTokCreatorInfoResponse(**info)


# ── TikTok status reconciliation ─────────────────────────────────


class RefreshTikTokStatusesResponse(BaseModel):
    matched: int
    scanned: int
    published: int
    pending_approval: int
    failed: int
    still_processing: int
    skipped: int
    skipped_no_publish_id: int
    details: list[dict]


@router.post("/refresh-tiktok-statuses", response_model=RefreshTikTokStatusesResponse)
async def refresh_tiktok_statuses(
    days: int = Query(default=14, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    settings_obj: Settings = Depends(get_settings),
):
    """Reconcile each TikTok post's DB status with what TikTok actually did.

    For unaudited Direct Post, the upload API returns publish_id immediately
    but the video sits in TikTok's processing queue and may end up in the
    creator's drafts (SEND_TO_USER_INBOX), be published (PUBLISH_COMPLETE),
    or be silently rejected (FAILED) — without any callback. This endpoint
    queries TikTok's status endpoint for each recent publish_id and
    rewrites the local post status to match.

    The window is matched against ANY of created_at/updated_at/scheduled_at/
    published_at — a post created 8 days ago but published 2 days ago should
    still be in scope. Posts that match the window but have no platform_post_id
    are surfaced separately with reason="no_publish_id" so it's obvious when
    a post never went through the API.
    """
    from datetime import timedelta
    from sqlalchemy import or_
    from app.services.adapter_factory import get_adapter

    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = await db.execute(
        select(PostModel).where(
            PostModel.tenant_id == tenant_id,
            PostModel.platform == "tiktok",
            or_(
                PostModel.created_at >= cutoff,
                PostModel.updated_at >= cutoff,
                PostModel.scheduled_at >= cutoff,
                PostModel.published_at >= cutoff,
            ),
        ).order_by(PostModel.created_at.desc())
    )
    matched_posts = list(rows.scalars().all())
    posts = [p for p in matched_posts if p.platform_post_id]
    no_publish_id_posts = [p for p in matched_posts if not p.platform_post_id]

    counts = {"published": 0, "pending_approval": 0, "failed": 0, "still_processing": 0, "skipped": 0}
    details: list[dict] = []

    # Cache adapters per channel so we don't refresh tokens once per post
    adapter_cache: dict[uuid.UUID, object] = {}

    for post in posts:
        publish_id = post.platform_post_id
        channel_id = post.channel_id
        if not channel_id:
            counts["skipped"] += 1
            details.append({"post_id": str(post.id), "publish_id": publish_id, "result": "no_channel"})
            continue

        try:
            if channel_id not in adapter_cache:
                adapter_cache[channel_id] = await get_adapter(
                    db, channel_id, settings_obj, require_publish=False,
                )
            adapter = adapter_cache[channel_id]
            publisher = getattr(adapter, "_publisher", None)
            if publisher is None:
                counts["skipped"] += 1
                details.append({"post_id": str(post.id), "publish_id": publish_id, "result": "no_publisher"})
                continue
            data = await publisher.get_post_status(publish_id)
        except Exception as exc:
            err_str = str(exc)
            # invalid_publish_id is a terminal "TikTok no longer recognizes
            # this upload" signal. Either the video was rejected and the
            # record was purged, or the publish aged out of TikTok's
            # retention window. Either way the post is NOT live on TikTok,
            # so reclassify as failed with a clear reason instead of
            # leaving it as an opaque error and pretending it's published.
            if "invalid_publish_id" in err_str:
                logger.info(
                    "TikTok purged publish_id for post %s (id=%s) — marking failed",
                    post.id, publish_id,
                )
                post.status = "failed"
                post.published_at = None
                post.last_error = (
                    "TikTok no longer recognizes this publish_id — the upload was "
                    "either rejected during processing or aged out before reaching "
                    "a terminal state. Republish if it should be live."
                )[:1000]
                counts["failed"] += 1
                details.append({
                    "post_id": str(post.id),
                    "publish_id": publish_id,
                    "result": "invalid_publish_id",
                    "previous_db_status": post.status,
                })
                continue

            logger.warning("TikTok status query failed for post %s (publish_id=%s): %s",
                           post.id, publish_id, exc)
            counts["skipped"] += 1
            details.append({
                "post_id": str(post.id),
                "publish_id": publish_id,
                "result": "error",
                "error": err_str[:300],
            })
            continue

        inner = data.get("data", {}) if isinstance(data, dict) else {}
        tt_status = inner.get("status")
        fail_reason = inner.get("fail_reason")

        if tt_status == "PUBLISH_COMPLETE":
            if post.status != "published":
                post.status = "published"
                post.last_error = None
                if not post.published_at:
                    post.published_at = datetime.utcnow()
            counts["published"] += 1
            outcome = "published"
        elif tt_status == "SEND_TO_USER_INBOX":
            if post.status != "pending_approval":
                post.status = "pending_approval"
                post.published_at = None
                post.last_error = None
            counts["pending_approval"] += 1
            outcome = "pending_approval"
        elif tt_status == "FAILED":
            post.status = "failed"
            post.published_at = None
            post.last_error = (f"TikTok rejected: {fail_reason}" if fail_reason else "TikTok rejected")[:1000]
            counts["failed"] += 1
            outcome = "failed"
        else:
            counts["still_processing"] += 1
            outcome = "still_processing"

        details.append({
            "post_id": str(post.id),
            "publish_id": publish_id,
            "tiktok_status": tt_status,
            "fail_reason": fail_reason,
            "result": outcome,
            "previous_db_status": post.status,
        })

    # Also surface posts that match the time window but don't have a
    # publish_id — these never went through the TikTok API (e.g. user
    # clicked Mark Published manually) so we can't query their status.
    for post in no_publish_id_posts:
        details.append({
            "post_id": str(post.id),
            "publish_id": None,
            "result": "no_publish_id",
            "previous_db_status": post.status,
        })

    await db.flush()

    return RefreshTikTokStatusesResponse(
        matched=len(matched_posts),
        scanned=len(posts),
        published=counts["published"],
        pending_approval=counts["pending_approval"],
        failed=counts["failed"],
        still_processing=counts["still_processing"],
        skipped=counts["skipped"],
        skipped_no_publish_id=len(no_publish_id_posts),
        details=details,
    )

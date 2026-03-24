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
    logger = logging.getLogger(__name__)
    try:
        repo = BaseRepository(db, PostModel, tenant_id)
        entity = await repo.create(**body.model_dump())
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
    repo = BaseRepository(db, PostModel, tenant_id)
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

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
    try:
        result = await svc.schedule_post(post_id, body.scheduled_at)
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
            changes={"scheduled_at": body.scheduled_at.isoformat()},
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


@router.post("/{post_id}/retry", response_model=PostActionResponse)
async def retry_post(
    post_id: uuid.UUID,
    svc: PublishingService = Depends(_get_publishing_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Re-queue a failed post for another publish attempt."""
    try:
        result = await svc.retry_post(post_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    try:
        await audit.log(
            action="post.retried",
            entity_type="post",
            entity_id=post_id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)
    return result

"""Campaign CRUD routes with launch/pause actions and audit logging."""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id, get_user_id, get_audit_service
from app.middleware.rbac import require_role
from app.schemas import (
    CampaignCreateRequest,
    CampaignUpdateRequest,
    CampaignResponse,
    CampaignDestinationReport,
    CampaignPlanResponse,
    CampaignPlanDayResponse,
    CalendarItemResponse,
    PostResponse,
)
from app.services.audit_service import AuditService
from amplify.db.models.campaign import CampaignModel
from amplify.db.repository import BaseRepository

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.get("", response_model=list[CampaignResponse])
@router.get("/", response_model=list[CampaignResponse])
async def list_campaigns(
    artist_id: uuid.UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """List campaigns, optionally filtered by artist_id and/or status."""
    repo = BaseRepository(db, CampaignModel, tenant_id)
    filters = {}
    if artist_id is not None:
        filters["artist_id"] = artist_id
    if status_filter is not None:
        filters["status"] = status_filter
    return await repo.list(**filters)


@router.post("", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    body: CampaignCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Create a new campaign."""
    repo = BaseRepository(db, CampaignModel, tenant_id)
    entity = await repo.create(**body.model_dump())

    try:
        await audit.log(
            action="campaign.created",
            entity_type="campaign",
            entity_id=entity.id,
            user_id=user_id,
            changes=body.model_dump(mode="json"),
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return entity


@router.get("/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Get a campaign by ID."""
    repo = BaseRepository(db, CampaignModel, tenant_id)
    entity = await repo.get(campaign_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return entity


@router.put("/{campaign_id}", response_model=CampaignResponse)
async def update_campaign(
    campaign_id: uuid.UUID,
    body: CampaignUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Update a campaign by ID."""
    repo = BaseRepository(db, CampaignModel, tenant_id)
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    entity = await repo.update(campaign_id, **updates)
    if entity is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    try:
        await audit.log(
            action="campaign.updated",
            entity_type="campaign",
            entity_id=entity.id,
            user_id=user_id,
            changes=updates,
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return entity


@router.get("/{campaign_id}/plan", response_model=CampaignPlanResponse)
async def get_campaign_plan(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Get the full plan for a campaign — posts and calendar items grouped by day."""
    from sqlalchemy import select
    from amplify.db.models.post import PostModel
    from amplify.db.models.calendar_item import CalendarItemModel
    from collections import defaultdict

    repo = BaseRepository(db, CampaignModel, tenant_id)
    campaign = await repo.get(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Load posts for this campaign
    posts_result = await db.execute(
        select(PostModel).where(
            PostModel.campaign_id == campaign_id,
            PostModel.tenant_id == tenant_id,
        ).order_by(PostModel.created_at)
    )
    posts = posts_result.scalars().all()

    # Load calendar items for this campaign
    cal_result = await db.execute(
        select(CalendarItemModel).where(
            CalendarItemModel.campaign_id == campaign_id,
            CalendarItemModel.tenant_id == tenant_id,
        ).order_by(CalendarItemModel.scheduled_date)
    )
    cal_items = cal_result.scalars().all()

    # Group by date
    days_map: dict[str, dict] = defaultdict(lambda: {"calendar_items": [], "posts": []})

    for item in cal_items:
        day_key = str(item.scheduled_date)
        days_map[day_key]["calendar_items"].append(item)

    for post in posts:
        if post.scheduled_at:
            day_key = str(post.scheduled_at.date())
        elif post.created_at:
            day_key = str(post.created_at.date())
        else:
            day_key = "unscheduled"
        days_map[day_key]["posts"].append(post)

    # Build response
    sorted_days = sorted(days_map.keys())
    days = []
    for i, day_key in enumerate(sorted_days):
        data = days_map[day_key]
        days.append(CampaignPlanDayResponse(
            day=day_key,
            day_number=i + 1,
            calendar_items=[CalendarItemResponse.model_validate(c) for c in data["calendar_items"]],
            posts=[PostResponse.model_validate(p) for p in data["posts"]],
        ))

    # Stats
    total = len(posts)
    pending = sum(1 for p in posts if p.approval_status == "pending_review")
    approved = sum(1 for p in posts if p.approval_status == "approved")
    rejected = sum(1 for p in posts if p.approval_status == "rejected")
    published = sum(1 for p in posts if p.status == "published")

    # Resolve the bio-link hint from the linked release. The artist should
    # point their IG/TikTok bio link at this URL — captions on those
    # platforms aren't clickable, so any goal=stream/save/purchase post on
    # IG/TikTok needs the bio link to actually convert.
    bio_link_hint: str | None = None
    if campaign.release_id:
        from amplify.db.models.release import ReleaseModel
        release_result = await db.execute(
            select(ReleaseModel).where(
                ReleaseModel.id == campaign.release_id,
                ReleaseModel.tenant_id == tenant_id,
            )
        )
        release = release_result.scalar_one_or_none()
        if release:
            bio_link_hint = (
                release.linktree_url
                or release.hyperfollow_url
                or release.bandcamp_url
                or None
            )

    # Count posts that depend on the bio link to convert — i.e. posts on
    # platforms where captions aren't clickable AND whose goal needs a
    # clickthrough. Helps the UI decide how loud to make the warning.
    _BIO_DEPENDENT_PLATFORMS = {"instagram", "tiktok"}
    _BIO_DEPENDENT_GOALS = {"stream", "save", "purchase", "follow"}
    bio_link_dependent_counts: dict[str, int] = {}
    for p in posts:
        if (p.platform or "").lower() in _BIO_DEPENDENT_PLATFORMS and (
            (getattr(p, "goal", None) or "").lower() in _BIO_DEPENDENT_GOALS
        ):
            key = (p.platform or "").lower()
            bio_link_dependent_counts[key] = bio_link_dependent_counts.get(key, 0) + 1

    return CampaignPlanResponse(
        campaign=CampaignResponse.model_validate(campaign),
        days=days,
        stats={
            "total_posts": total,
            "pending_review": pending,
            "approved": approved,
            "rejected": rejected,
            "published": published,
        },
        goal_mix=(campaign.config or {}).get("goal_mix", {}) if isinstance(campaign.config, dict) else {},
        bio_link_hint=bio_link_hint,
        bio_link_dependent_counts=bio_link_dependent_counts,
    )


@router.post("/{campaign_id}/approve-all")
async def approve_all_posts(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
):
    """Bulk-approve all pending_review posts.

    Media generation is NOT triggered here — the frontend calls
    POST /posts/{id}/generate-media sequentially for each post.
    """
    from sqlalchemy import update
    from amplify.db.models.post import PostModel

    # Approve all pending posts
    result = await db.execute(
        update(PostModel)
        .where(
            PostModel.campaign_id == campaign_id,
            PostModel.tenant_id == tenant_id,
            PostModel.approval_status == "pending_review",
        )
        .values(approval_status="approved")
        .returning(PostModel.id)
    )
    approved_ids = [row[0] for row in result.all()]
    await db.flush()

    return {
        "approved_count": len(approved_ids),
        "approved_ids": [str(pid) for pid in approved_ids],
    }


@router.post("/{campaign_id}/sync-calendar")
async def sync_campaign_calendar(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Rebuild calendar items from current campaign posts.

    Deletes existing calendar items for the campaign and recreates them
    from the current posts, so the calendar stays in sync after edits/deletes.
    """
    from sqlalchemy import select, delete as sql_delete
    from amplify.db.models.post import PostModel
    from amplify.db.models.calendar_item import CalendarItemModel
    from datetime import time as dt_time

    # Load current posts
    posts_result = await db.execute(
        select(PostModel).where(
            PostModel.campaign_id == campaign_id,
            PostModel.tenant_id == tenant_id,
        ).order_by(PostModel.created_at)
    )
    posts = posts_result.scalars().all()

    # Delete existing calendar items for this campaign
    await db.execute(
        sql_delete(CalendarItemModel).where(
            CalendarItemModel.campaign_id == campaign_id,
            CalendarItemModel.tenant_id == tenant_id,
        )
    )

    # Stagger times for posts on the same day
    TIMES = [
        dt_time(9, 0), dt_time(10, 30), dt_time(12, 0), dt_time(14, 0),
        dt_time(16, 30), dt_time(18, 0), dt_time(19, 30), dt_time(21, 0),
    ]
    day_counters: dict[str, int] = {}

    created = 0
    for post in posts:
        scheduled_date = None
        if post.scheduled_at:
            scheduled_date = post.scheduled_at.date()
        elif post.created_at:
            scheduled_date = post.created_at.date()
        if not scheduled_date:
            continue

        day_key = str(scheduled_date)
        idx = day_counters.get(day_key, 0)
        day_counters[day_key] = idx + 1

        action_label = post.action_type_label or "post"
        caption_preview = (post.content_text or "")[:80]
        title = f"{post.platform} {action_label}: {caption_preview}"

        item = CalendarItemModel(
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            title=title[:255],
            description=post.content_text or "",
            item_type=action_label if action_label in ("reel", "story", "email", "ad") else "post",
            scheduled_date=scheduled_date,
            scheduled_time=TIMES[idx % len(TIMES)],
            is_completed=post.status == "published",
        )
        db.add(item)
        created += 1

    await db.flush()
    return {"synced": created, "campaign_id": str(campaign_id)}


@router.delete(
    "/{campaign_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("admin"))],
)
async def delete_campaign(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Delete a campaign and all related records."""
    from sqlalchemy import select, text
    from amplify.db.models.post import PostModel

    # Verify campaign exists
    repo = BaseRepository(db, CampaignModel, tenant_id)
    campaign = await repo.get(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Delete child records that reference posts in this campaign
    post_result = await db.execute(
        select(PostModel.id).where(
            PostModel.campaign_id == campaign_id,
            PostModel.tenant_id == tenant_id,
        )
    )
    post_ids = [row[0] for row in post_result.all()]

    for pid in post_ids:
        for child_table in ("learning_events", "post_feature_vectors", "post_outcomes", "approvals"):
            try:
                await db.execute(
                    text(f"DELETE FROM {child_table} WHERE post_id = :pid"),
                    {"pid": str(pid)},
                )
            except Exception:
                pass

    # Delete posts, calendar items, and assets linked to this campaign
    for table in ("posts", "calendar_items", "assets"):
        try:
            await db.execute(
                text(f"DELETE FROM {table} WHERE campaign_id = :cid AND tenant_id = :tid"),
                {"cid": str(campaign_id), "tid": str(tenant_id)},
            )
        except Exception as exc:
            logger.warning("Cleanup %s for campaign %s failed: %s", table, campaign_id, exc)

    # Delete the campaign itself
    await repo.delete(campaign_id)

    try:
        await audit.log(
            action="campaign.deleted",
            entity_type="campaign",
            entity_id=campaign_id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{campaign_id}/launch", response_model=CampaignResponse)
async def launch_campaign(
    campaign_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Set campaign status to active and auto-schedule approved posts."""
    from sqlalchemy import select as sa_select
    from amplify.db.models.post import PostModel

    repo = BaseRepository(db, CampaignModel, tenant_id)
    entity = await repo.update(campaign_id, status="active")
    if entity is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Auto-schedule all approved/draft posts that have a scheduled_at time
    result = await db.execute(
        sa_select(PostModel).where(
            PostModel.campaign_id == campaign_id,
            PostModel.tenant_id == tenant_id,
            PostModel.status.in_(("approved", "draft")),
            PostModel.scheduled_at.isnot(None),
        )
    )
    posts = result.scalars().all()
    scheduled_count = 0
    for post in posts:
        post.status = "scheduled"
        scheduled_count += 1
    if scheduled_count:
        await db.flush()
        logger.info("Campaign %s launched: auto-scheduled %d posts", campaign_id, scheduled_count)

    # Enqueue content generation for any draft posts that still have
    # brief-style content (short planner output, not real captions).
    try:
        from worker.app.queue import JobQueue
        r = request.app.state.redis  # type: ignore[union-attr]
        q = JobQueue(r)
        await q.enqueue(
            "generate_content",
            {
                "campaign_id": str(campaign_id),
                "tenant_id": str(tenant_id),
                "user_id": str(user_id) if user_id else None,
            },
            idempotency_key=f"launch_content_{campaign_id}",
        )
    except Exception as exc:
        logger.warning("Failed to enqueue content generation on launch: %s", exc)

    try:
        await audit.log(
            action="campaign.launched",
            entity_type="campaign",
            entity_id=entity.id,
            user_id=user_id,
            changes={"status": "active", "auto_scheduled_posts": scheduled_count},
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return entity


@router.post("/{campaign_id}/pause", response_model=CampaignResponse)
async def pause_campaign(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Set campaign status to paused and unschedule pending posts."""
    from sqlalchemy import select as sa_select
    from amplify.db.models.post import PostModel

    repo = BaseRepository(db, CampaignModel, tenant_id)
    entity = await repo.update(campaign_id, status="paused")
    if entity is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Revert scheduled posts back to draft so they don't publish
    result = await db.execute(
        sa_select(PostModel).where(
            PostModel.campaign_id == campaign_id,
            PostModel.tenant_id == tenant_id,
            PostModel.status == "scheduled",
        )
    )
    posts = result.scalars().all()
    unscheduled_count = 0
    for post in posts:
        post.status = "draft"
        unscheduled_count += 1
    if unscheduled_count:
        await db.flush()
        logger.info("Campaign %s paused: unscheduled %d posts", campaign_id, unscheduled_count)

    try:
        await audit.log(
            action="campaign.paused",
            entity_type="campaign",
            entity_id=entity.id,
            user_id=user_id,
            changes={"status": "paused", "unscheduled_posts": unscheduled_count},
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return entity


@router.get("/{campaign_id}/destinations", response_model=CampaignDestinationReport)
async def check_campaign_destinations(
    campaign_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Validate that every post in a campaign has a destination URL.

    Returns the canonical CTA URL and a list of posts missing destinations.
    """
    from app.services.destination_service import DestinationService

    service = DestinationService(db, tenant_id)

    # Verify campaign exists
    repo = BaseRepository(db, CampaignModel, tenant_id)
    campaign = await repo.get(campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return await service.validate_campaign_destinations(campaign_id)

"""Campaign CRUD routes with launch/pause actions and audit logging."""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
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
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Set campaign status to active."""
    repo = BaseRepository(db, CampaignModel, tenant_id)
    entity = await repo.update(campaign_id, status="active")
    if entity is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    try:
        await audit.log(
            action="campaign.launched",
            entity_type="campaign",
            entity_id=entity.id,
            user_id=user_id,
            changes={"status": "active"},
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
    """Set campaign status to paused."""
    repo = BaseRepository(db, CampaignModel, tenant_id)
    entity = await repo.update(campaign_id, status="paused")
    if entity is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    try:
        await audit.log(
            action="campaign.paused",
            entity_type="campaign",
            entity_id=entity.id,
            user_id=user_id,
            changes={"status": "paused"},
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

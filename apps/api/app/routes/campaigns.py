"""Campaign CRUD routes with launch/pause actions and audit logging."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id, get_user_id, get_audit_service
from app.middleware.rbac import require_role
from app.schemas import (
    CampaignCreateRequest,
    CampaignUpdateRequest,
    CampaignResponse,
    CampaignDestinationReport,
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

    await audit.log(
        action="campaign.created",
        entity_type="campaign",
        entity_id=entity.id,
        user_id=user_id,
        changes=body.model_dump(),
    )

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

    await audit.log(
        action="campaign.updated",
        entity_type="campaign",
        entity_id=entity.id,
        user_id=user_id,
        changes=updates,
    )

    return entity


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
    """Delete a campaign by ID. Admin+ only."""
    repo = BaseRepository(db, CampaignModel, tenant_id)
    deleted = await repo.delete(campaign_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Campaign not found")

    await audit.log(
        action="campaign.deleted",
        entity_type="campaign",
        entity_id=campaign_id,
        user_id=user_id,
    )

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

    await audit.log(
        action="campaign.launched",
        entity_type="campaign",
        entity_id=entity.id,
        user_id=user_id,
        changes={"status": "active"},
    )

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

    await audit.log(
        action="campaign.paused",
        entity_type="campaign",
        entity_id=entity.id,
        user_id=user_id,
        changes={"status": "paused"},
    )

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

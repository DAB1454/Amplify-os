"""Campaign template routes — create, list, clone across tenants."""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id, get_user_id, get_audit_service
from app.middleware.rbac import require_role
from app.schemas import (
    CampaignTemplateCreateRequest,
    CampaignTemplateResponse,
    CloneTemplateRequest,
)
from app.services.audit_service import AuditService
from amplify.billing.enforcement import SubscriptionEnforcer
from amplify.billing.metering import MeteringService
from amplify.db.models.campaign import CampaignModel
from amplify.db.models.campaign_template import CampaignTemplateModel

router = APIRouter(prefix="/campaign-templates", tags=["campaign-templates"])

_metering = MeteringService()


@router.get("", response_model=list[CampaignTemplateResponse])
@router.get("/", response_model=list[CampaignTemplateResponse])
async def list_templates(
    category: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """List templates visible to this tenant (own + global)."""
    query = (
        select(CampaignTemplateModel)
        .where(
            CampaignTemplateModel.is_active == True,
            or_(
                CampaignTemplateModel.tenant_id == tenant_id,
                CampaignTemplateModel.is_global == True,
            ),
        )
        .order_by(CampaignTemplateModel.created_at.desc())
    )
    if category:
        query = query.where(CampaignTemplateModel.category == category)
    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("", response_model=CampaignTemplateResponse, status_code=201)
@router.post("/", response_model=CampaignTemplateResponse, status_code=201)
async def create_template(
    body: CampaignTemplateCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Create a campaign template for this tenant."""
    template = CampaignTemplateModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        name=body.name,
        description=body.description,
        phase=body.phase,
        goals=body.goals,
        config=body.config,
        post_templates=body.post_templates,
        is_global=False,  # only admins can make global
        category=body.category,
        tags=body.tags,
    )
    db.add(template)
    await db.flush()
    await db.refresh(template)

    try:
        await audit.log(
            action="campaign_template.created",
            entity_type="campaign_template",
            entity_id=template.id,
            user_id=user_id,
            changes=body.model_dump(mode="json"),
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)
    return template


@router.get("/{template_id}", response_model=CampaignTemplateResponse)
async def get_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Get a template by ID (must be own or global)."""
    result = await db.execute(
        select(CampaignTemplateModel).where(
            CampaignTemplateModel.id == template_id,
            or_(
                CampaignTemplateModel.tenant_id == tenant_id,
                CampaignTemplateModel.is_global == True,
            ),
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.post("/{template_id}/clone")
async def clone_template(
    template_id: uuid.UUID,
    body: CloneTemplateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Clone a template into a new campaign for this tenant."""
    # Check campaign limit
    from amplify.db.models.tenant import TenantModel
    tenant_result = await db.execute(
        select(TenantModel).where(TenantModel.id == tenant_id)
    )
    tenant = tenant_result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    enforcer = SubscriptionEnforcer(db, tenant_id, tenant.plan_tier, _metering)
    check = await enforcer.can_add_campaign()
    if not check.allowed:
        raise HTTPException(status_code=403, detail=check.message)

    # Fetch template
    result = await db.execute(
        select(CampaignTemplateModel).where(
            CampaignTemplateModel.id == template_id,
            or_(
                CampaignTemplateModel.tenant_id == tenant_id,
                CampaignTemplateModel.is_global == True,
            ),
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    # Create campaign from template
    campaign = CampaignModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        artist_id=body.artist_id or uuid.UUID(int=0),
        release_id=body.release_id,
        name=body.name or f"{template.name} (copy)",
        status="draft",
        phase=template.phase,
        goals=dict(template.goals),
        config={**dict(template.config), "from_template": str(template_id)},
    )
    db.add(campaign)
    await db.flush()
    await db.refresh(campaign)

    try:
        await audit.log(
            action="campaign.cloned_from_template",
            entity_type="campaign",
            entity_id=campaign.id,
            user_id=user_id,
            changes={"template_id": str(template_id), "campaign_name": campaign.name},
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return {
        "campaign_id": str(campaign.id),
        "name": campaign.name,
        "template_id": str(template_id),
        "status": "draft",
    }


@router.delete(
    "/{template_id}",
    status_code=204,
    dependencies=[Depends(require_role("admin"))],
)
async def delete_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Delete a template (own only, admin+)."""
    result = await db.execute(
        select(CampaignTemplateModel).where(
            CampaignTemplateModel.id == template_id,
            CampaignTemplateModel.tenant_id == tenant_id,
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    await db.delete(template)

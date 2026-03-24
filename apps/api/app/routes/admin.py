"""Admin console routes for support operators.

All routes require the 'admin' role or higher. In a production system
these would be further restricted to a separate admin panel with
additional authentication.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_user_id, get_audit_service
from app.middleware.rbac import require_role
from app.services.audit_service import AuditService
from amplify.billing.plans import get_plan
from amplify.db.models.tenant import TenantModel
from amplify.db.models.artist import ArtistModel
from amplify.db.models.campaign import CampaignModel
from amplify.db.models.campaign_template import CampaignTemplateModel
from amplify.db.models.channel import ChannelConnectionModel
from amplify.db.models.membership import MembershipModel
from amplify.db.models.post import PostModel
from amplify.db.models.user import UserModel
from amplify.db.models.audit_log import AuditLogModel

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_role("admin"))],
)


# ── Tenant management ────────────────────────────────────────────


@router.get("/tenants")
async def list_all_tenants(
    is_active: bool | None = Query(default=None),
    plan_tier: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List all tenants with summary stats."""
    query = select(TenantModel).order_by(TenantModel.created_at.desc())
    if is_active is not None:
        query = query.where(TenantModel.is_active == is_active)
    if plan_tier:
        query = query.where(TenantModel.plan_tier == plan_tier)
    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    tenants = result.scalars().all()

    summaries = []
    for t in tenants:
        # Count artists
        artist_count = (await db.execute(
            select(func.count()).select_from(ArtistModel)
            .where(ArtistModel.tenant_id == t.id)
        )).scalar_one()

        # Count campaigns
        campaign_count = (await db.execute(
            select(func.count()).select_from(CampaignModel)
            .where(CampaignModel.tenant_id == t.id)
        )).scalar_one()

        # Count channels
        channel_count = (await db.execute(
            select(func.count()).select_from(ChannelConnectionModel)
            .where(ChannelConnectionModel.tenant_id == t.id)
        )).scalar_one()

        # Count members
        member_count = (await db.execute(
            select(func.count()).select_from(MembershipModel)
            .where(MembershipModel.tenant_id == t.id)
        )).scalar_one()

        summaries.append({
            "id": str(t.id),
            "name": t.name,
            "slug": t.slug,
            "plan_tier": t.plan_tier,
            "is_active": t.is_active,
            "onboarding_completed": t.onboarding_completed,
            "stripe_customer_id": t.stripe_customer_id,
            "artists": artist_count,
            "campaigns": campaign_count,
            "channels": channel_count,
            "members": member_count,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })

    return summaries


@router.get("/tenants/{tenant_id}")
async def get_tenant_detail(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get detailed info for a specific tenant."""
    result = await db.execute(
        select(TenantModel).where(TenantModel.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    plan = get_plan(tenant.plan_tier)

    return {
        "id": str(tenant.id),
        "name": tenant.name,
        "slug": tenant.slug,
        "plan_tier": tenant.plan_tier,
        "plan_name": plan.name if plan else tenant.plan_tier,
        "is_active": tenant.is_active,
        "onboarding_completed": tenant.onboarding_completed,
        "stripe_customer_id": tenant.stripe_customer_id,
        "stripe_subscription_id": tenant.stripe_subscription_id,
        "settings": tenant.settings or {},
        "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
        "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None,
    }


@router.post("/tenants/{tenant_id}/change-plan")
async def admin_change_plan(
    tenant_id: uuid.UUID,
    plan_tier: str = Query(...),
    db: AsyncSession = Depends(get_db),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Change a tenant's plan (admin operation)."""
    plan = get_plan(plan_tier)
    if plan is None:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan_tier}")

    result = await db.execute(
        select(TenantModel).where(TenantModel.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    old_tier = tenant.plan_tier
    tenant.plan_tier = plan_tier

    try:
        await audit.log(
            action="admin.plan_changed",
            entity_type="tenant",
            entity_id=tenant_id,
            user_id=user_id,
            changes={"from": old_tier, "to": plan_tier},
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return {"tenant_id": str(tenant_id), "plan_tier": plan_tier}


@router.post("/tenants/{tenant_id}/toggle-active")
async def toggle_tenant_active(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Enable or disable a tenant."""
    result = await db.execute(
        select(TenantModel).where(TenantModel.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant.is_active = not tenant.is_active

    try:
        await audit.log(
            action="admin.tenant_toggled",
            entity_type="tenant",
            entity_id=tenant_id,
            user_id=user_id,
            changes={"is_active": tenant.is_active},
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return {"tenant_id": str(tenant_id), "is_active": tenant.is_active}


# ── Global templates ─────────────────────────────────────────────


@router.post("/campaign-templates/{template_id}/make-global")
async def make_template_global(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Promote a template to global (visible to all tenants)."""
    result = await db.execute(
        select(CampaignTemplateModel).where(
            CampaignTemplateModel.id == template_id,
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    template.is_global = True

    try:
        await audit.log(
            action="admin.template_made_global",
            entity_type="campaign_template",
            entity_id=template_id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return {"template_id": str(template_id), "is_global": True}


# ── Platform stats ───────────────────────────────────────────────


@router.get("/stats")
async def platform_stats(db: AsyncSession = Depends(get_db)):
    """High-level platform stats for the admin dashboard."""
    tenants = (await db.execute(select(func.count()).select_from(TenantModel))).scalar_one()
    active_tenants = (await db.execute(
        select(func.count()).select_from(TenantModel)
        .where(TenantModel.is_active == True)
    )).scalar_one()
    artists = (await db.execute(select(func.count()).select_from(ArtistModel))).scalar_one()
    campaigns = (await db.execute(select(func.count()).select_from(CampaignModel))).scalar_one()
    posts = (await db.execute(select(func.count()).select_from(PostModel))).scalar_one()
    channels = (await db.execute(select(func.count()).select_from(ChannelConnectionModel))).scalar_one()

    # Tier breakdown
    tier_result = await db.execute(
        select(TenantModel.plan_tier, func.count())
        .group_by(TenantModel.plan_tier)
    )
    tier_breakdown = {row[0]: row[1] for row in tier_result.all()}

    return {
        "tenants": tenants,
        "active_tenants": active_tenants,
        "artists": artists,
        "campaigns": campaigns,
        "posts": posts,
        "channels": channels,
        "tier_breakdown": tier_breakdown,
    }


# ── Audit log ────────────────────────────────────────────────────


@router.get("/audit-log")
async def global_audit_log(
    tenant_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Query audit log across all tenants or filtered."""
    query = select(AuditLogModel).order_by(AuditLogModel.created_at.desc())
    if tenant_id:
        query = query.where(AuditLogModel.tenant_id == tenant_id)
    if action:
        query = query.where(AuditLogModel.action == action)
    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    logs = result.scalars().all()
    return [
        {
            "id": str(log.id),
            "tenant_id": str(log.tenant_id),
            "user_id": str(log.user_id) if log.user_id else None,
            "action": log.action,
            "entity_type": log.entity_type,
            "entity_id": str(log.entity_id) if log.entity_id else None,
            "changes": log.changes or {},
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]

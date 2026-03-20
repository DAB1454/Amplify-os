"""Onboarding wizard routes — track tenant setup progress."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id
from amplify.core.domain.tenant import ONBOARDING_STEPS
from amplify.db.models.artist import ArtistModel
from amplify.db.models.campaign import CampaignModel
from amplify.db.models.channel import ChannelConnectionModel
from amplify.db.models.tenant import TenantModel

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


async def _compute_steps(db: AsyncSession, tenant_id: uuid.UUID) -> list[dict]:
    """Compute completion status for each onboarding step by querying the DB."""
    # Count entities
    artists = (await db.execute(
        select(func.count()).select_from(ArtistModel)
        .where(ArtistModel.tenant_id == tenant_id)
    )).scalar_one()

    channels = (await db.execute(
        select(func.count()).select_from(ChannelConnectionModel)
        .where(ChannelConnectionModel.tenant_id == tenant_id)
    )).scalar_one()

    campaigns = (await db.execute(
        select(func.count()).select_from(CampaignModel)
        .where(CampaignModel.tenant_id == tenant_id)
    )).scalar_one()

    tenant_result = await db.execute(
        select(TenantModel).where(TenantModel.id == tenant_id)
    )
    tenant = tenant_result.scalar_one_or_none()
    has_plan = tenant and tenant.plan_tier != "solo"

    completion_map = {
        "create_artist": artists > 0,
        "connect_channel": channels > 0,
        "add_release": False,  # releases need a separate check
        "create_campaign": campaigns > 0,
        "choose_plan": has_plan,
    }

    # Check releases via artist
    if artists > 0:
        from amplify.db.models.release import ReleaseModel
        releases = (await db.execute(
            select(func.count()).select_from(ReleaseModel)
            .where(ReleaseModel.tenant_id == tenant_id)
        )).scalar_one()
        completion_map["add_release"] = releases > 0

    steps = []
    for step in ONBOARDING_STEPS:
        steps.append({
            "key": step.key,
            "label": step.label,
            "description": step.description,
            "is_completed": completion_map.get(step.key, False),
            "is_required": step.is_required,
        })
    return steps


@router.get("/status")
async def onboarding_status(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Return the onboarding progress for the current tenant."""
    steps = await _compute_steps(db, tenant_id)

    required_steps = [s for s in steps if s["is_required"]]
    required_done = sum(1 for s in required_steps if s["is_completed"])
    all_done = sum(1 for s in steps if s["is_completed"])
    total = len(steps)
    progress_pct = round(all_done / total * 100) if total > 0 else 0

    # Check if tenant marked onboarding complete
    tenant_result = await db.execute(
        select(TenantModel).where(TenantModel.id == tenant_id)
    )
    tenant = tenant_result.scalar_one_or_none()
    completed = (tenant and tenant.onboarding_completed) or (
        required_done == len(required_steps)
    )

    return {
        "completed": completed,
        "steps": steps,
        "progress_pct": progress_pct,
    }


@router.post("/complete")
async def complete_onboarding(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Mark onboarding as complete for the current tenant."""
    result = await db.execute(
        select(TenantModel).where(TenantModel.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant.onboarding_completed = True
    return {"completed": True}


@router.get("/recommendations")
async def onboarding_recommendations(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Get smart default recommendations for new tenants during onboarding.

    Returns global priors grouped by category (posting times, CTA styles, etc.)
    with clear labeling that these are aggregate defaults, not tenant-specific.
    """
    from app.services.blended_recommendation_service import BlendedRecommendationService

    svc = BlendedRecommendationService(db, tenant_id)
    return await svc.get_onboarding_recommendations()


@router.post("/dismiss")
async def dismiss_onboarding(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Dismiss onboarding without completing all steps."""
    result = await db.execute(
        select(TenantModel).where(TenantModel.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    tenant.onboarding_completed = True
    return {"completed": True, "dismissed": True}

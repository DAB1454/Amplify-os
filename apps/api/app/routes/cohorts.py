"""Cohort definition and aggregation routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id, get_user_id
from app.middleware.rbac import require_role
from app.services.cohort_service import CohortService

router = APIRouter(prefix="/cohorts", tags=["cohorts"])


# ── Request/response schemas ─────────────────────────────────────


class CohortDefinitionCreate(BaseModel):
    name: str = Field(max_length=255)
    description: str = ""
    criteria: dict


class CohortDefinitionUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    criteria: dict | None = None
    is_active: bool | None = None


class TenantTraits(BaseModel):
    """Traits for a tenant profile used in assignment/aggregation."""
    traits: dict = Field(default_factory=dict)
    observation_count: int = 0
    rewards: list[float] = Field(default_factory=list)


class ColdStartRequest(BaseModel):
    """Request cold-start priors for a tenant."""
    tenant_traits: TenantTraits
    all_profiles: list[TenantTraits] = Field(default_factory=list)
    feature_names: list[str] | None = None


# ── Dependencies ─────────────────────────────────────────────────


def _get_service(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> CohortService:
    return CohortService(db, tenant_id)


# ── Routes ───────────────────────────────────────────────────────


@router.post(
    "/definitions",
    summary="Create a cohort definition",
    dependencies=[Depends(require_role("admin"))],
)
async def create_definition(
    body: CohortDefinitionCreate,
    svc: CohortService = Depends(_get_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
):
    return await svc.create_definition(body.model_dump(), user_id)


@router.get(
    "/definitions",
    summary="List cohort definitions",
)
async def list_definitions(
    active_only: bool = True,
    svc: CohortService = Depends(_get_service),
):
    return await svc.list_definitions(active_only=active_only)


@router.get(
    "/definitions/{defn_id}",
    summary="Get a cohort definition",
)
async def get_definition(
    defn_id: uuid.UUID,
    svc: CohortService = Depends(_get_service),
):
    result = await svc.get_definition(defn_id)
    if not result:
        raise HTTPException(status_code=404, detail="Cohort definition not found")
    return result


@router.put(
    "/definitions/{defn_id}",
    summary="Update a cohort definition",
    dependencies=[Depends(require_role("admin"))],
)
async def update_definition(
    defn_id: uuid.UUID,
    body: CohortDefinitionUpdate,
    svc: CohortService = Depends(_get_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
    result = await svc.update_definition(defn_id, updates, user_id)
    if not result:
        raise HTTPException(status_code=404, detail="Cohort definition not found")
    return result


@router.delete(
    "/definitions/{defn_id}",
    summary="Delete a cohort definition",
    dependencies=[Depends(require_role("admin"))],
)
async def delete_definition(
    defn_id: uuid.UUID,
    svc: CohortService = Depends(_get_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
):
    deleted = await svc.delete_definition(defn_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Cohort definition not found")
    return {"deleted": True}


@router.post(
    "/assign",
    summary="Assign a tenant to matching cohorts",
)
async def assign_tenant(
    body: TenantTraits,
    svc: CohortService = Depends(_get_service),
):
    from amplify.learning.cohorts.engine import TenantProfile

    profile = TenantProfile(
        tenant_id=svc.tenant_id,
        traits=body.traits,
        observation_count=body.observation_count,
        rewards=body.rewards,
    )
    return {"assignments": await svc.assign_tenant(profile)}


@router.post(
    "/cold-start-priors",
    summary="Compute cold-start priors from matching cohorts",
)
async def cold_start_priors(
    body: ColdStartRequest,
    svc: CohortService = Depends(_get_service),
):
    from amplify.learning.cohorts.engine import TenantProfile

    tenant_profile = TenantProfile(
        tenant_id=svc.tenant_id,
        traits=body.tenant_traits.traits,
        observation_count=body.tenant_traits.observation_count,
        rewards=body.tenant_traits.rewards,
    )
    all_profiles = [
        TenantProfile(
            tenant_id=uuid.uuid4(),  # anonymous
            traits=p.traits,
            observation_count=p.observation_count,
            rewards=p.rewards,
        )
        for p in body.all_profiles
    ]
    priors = await svc.get_cold_start_priors(
        tenant_profile,
        all_profiles,
        feature_names=body.feature_names,
    )
    return {"priors": [p.to_dict() for p in priors]}


@router.post(
    "/seed",
    summary="Seed sample cohort definitions",
    dependencies=[Depends(require_role("admin"))],
)
async def seed_definitions(
    svc: CohortService = Depends(_get_service),
):
    created = await svc.seed_sample_definitions()
    return {"created": len(created), "definitions": created}

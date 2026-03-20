"""Global priors routes — privacy-safe cross-tenant intelligence."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id
from app.services.global_prior_service import DBGlobalPriorService

router = APIRouter(prefix="/global-priors", tags=["global-priors"])


# ── Dependencies ─────────────────────────────────────────────────


def _get_service(
    db: AsyncSession = Depends(get_db),
) -> DBGlobalPriorService:
    return DBGlobalPriorService(db)


# ── Routes ───────────────────────────────────────────────────────


@router.get(
    "",
    summary="List active global priors",
)
async def list_priors(
    category: str | None = Query(default=None, description="Filter by category"),
    source_cohort_id: uuid.UUID | None = Query(default=None, description="Filter by source cohort"),
    svc: DBGlobalPriorService = Depends(_get_service),
):
    priors = await svc.list_active_priors(
        category=category,
        source_cohort_id=source_cohort_id,
    )
    return {"priors": priors, "count": len(priors)}


@router.get(
    "/recommendations",
    summary="Get cold-start recommendations for a tenant",
)
async def get_recommendations(
    tenant_observation_count: int = Query(
        default=0, ge=0, description="How many observations the tenant has",
    ),
    category: str | None = Query(default=None, description="Filter by category"),
    svc: DBGlobalPriorService = Depends(_get_service),
):
    recs = await svc.get_cold_start_recommendations(
        tenant_observation_count=tenant_observation_count,
        category=category,
    )
    total = sum(len(v) for v in recs.values())
    return {
        "recommendations": recs,
        "total": total,
        "source": "global_prior",
        "note": "These are aggregate patterns from similar accounts, not learned from your data.",
    }


@router.get(
    "/categories",
    summary="List available pattern categories",
)
async def list_categories():
    from amplify.learning.global_priors.service import PATTERN_CATEGORIES, CATEGORY_FEATURE_MAP
    return {
        "categories": [
            {
                "name": cat,
                "features": CATEGORY_FEATURE_MAP.get(cat, []),
            }
            for cat in PATTERN_CATEGORIES
        ],
    }

"""Learning event capture and tenant pattern routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id, get_user_id
from app.schemas.learning import (
    BackfillReportResponse,
    BackfillRequest,
    BlendedRecommendationResponse,
    BlendedRecommendationsResponse,
    DataMaturityStatus,
    IngestRequest,
    IngestResponse,
    LearningEventCreate,
    LearningEventResponse,
    PatternDataStatus,
    PatternsListResponse,
    RecommendationsListResponse,
    RecommendationResponse,
    TenantPatternResponse,
)
from app.services.blended_recommendation_service import BlendedRecommendationService
from app.services.learning_event_service import LearningEventService
from app.services.tenant_pattern_service import TenantPatternService
from amplify.db.models.learning import LearningEventModel

router = APIRouter(prefix="/learning", tags=["learning"])


def _get_learning_service(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> LearningEventService:
    return LearningEventService(db, tenant_id)


@router.post(
    "/events/ingest",
    response_model=IngestResponse,
    summary="Ingest learning events (batch, idempotent)",
)
async def ingest_events(
    body: IngestRequest,
    svc: LearningEventService = Depends(_get_learning_service),
):
    """Ingest a batch of learning events.

    Supports historical backfill and live traffic.
    Events with duplicate idempotency_keys are silently skipped.
    """
    raw_events = [e.model_dump() for e in body.events]
    result = await svc.ingest_batch(raw_events, source="api_ingest")
    return result


@router.post(
    "/events",
    response_model=LearningEventResponse,
    status_code=201,
    summary="Emit a single learning event",
)
async def emit_event(
    body: LearningEventCreate,
    svc: LearningEventService = Depends(_get_learning_service),
):
    """Emit a single learning event. Idempotent if idempotency_key is provided."""
    event = await svc.emit(**body.model_dump())
    return event


@router.get(
    "/events",
    response_model=list[LearningEventResponse],
    summary="List learning events",
)
async def list_events(
    action_type: str | None = Query(default=None),
    post_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """List learning events for the current tenant."""
    stmt = (
        select(LearningEventModel)
        .where(LearningEventModel.tenant_id == tenant_id)
        .order_by(LearningEventModel.observed_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if action_type:
        stmt = stmt.where(LearningEventModel.action_type == action_type)
    if post_id:
        stmt = stmt.where(LearningEventModel.post_id == post_id)

    result = await db.execute(stmt)
    return list(result.scalars().all())


# ── Tenant pattern endpoints ─────────────────────────────────────


def _get_pattern_service(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> TenantPatternService:
    return TenantPatternService(db, tenant_id)


@router.get(
    "/tenant/{tenant_id}/patterns",
    response_model=PatternsListResponse,
    summary="Get learned patterns for a tenant",
)
async def get_tenant_patterns(
    tenant_id: uuid.UUID,
    pattern_type: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    svc: TenantPatternService = Depends(_get_pattern_service),
):
    """Get all active learned patterns for a tenant.

    Includes evidence, confidence scores, and data status.
    If not enough data, patterns list will be empty and data_status
    will indicate what's needed.
    """
    patterns = await svc.get_patterns(
        pattern_type=pattern_type, direction=direction
    )
    data_status = await svc.get_data_status()
    return PatternsListResponse(
        patterns=patterns,
        data_status=PatternDataStatus(**data_status),
    )


@router.get(
    "/tenant/{tenant_id}/recommendations",
    response_model=RecommendationsListResponse,
    summary="Get recommendations for a tenant",
)
async def get_tenant_recommendations(
    tenant_id: uuid.UUID,
    svc: TenantPatternService = Depends(_get_pattern_service),
):
    """Generate actionable recommendations based on learned patterns.

    Each recommendation includes evidence and a confidence score.
    Pinned preferences are included with confidence=1.0.
    Returns empty recommendations with data_status if not enough data.
    """
    recs = await svc.get_recommendations()
    data_status = await svc.get_data_status()
    return RecommendationsListResponse(
        recommendations=[RecommendationResponse(**r) for r in recs],
        data_status=PatternDataStatus(**data_status),
    )


@router.post(
    "/tenant/{tenant_id}/patterns/update",
    response_model=list[TenantPatternResponse],
    summary="Run pattern learning (nightly job)",
)
async def update_tenant_patterns(
    tenant_id: uuid.UUID,
    svc: TenantPatternService = Depends(_get_pattern_service),
):
    """Trigger pattern learning for a tenant.

    Normally runs as a nightly job but can be triggered manually.
    Discovers new patterns, updates existing ones, and deactivates stale ones.
    Pinned patterns are preserved.
    """
    patterns = await svc.update_patterns()
    return patterns


class PinRequest(BaseModel):
    user_id: uuid.UUID


@router.post(
    "/tenant/{tenant_id}/patterns/{pattern_id}/pin",
    response_model=TenantPatternResponse,
    summary="Pin a pattern",
)
async def pin_pattern(
    tenant_id: uuid.UUID,
    pattern_id: uuid.UUID,
    body: PinRequest,
    svc: TenantPatternService = Depends(_get_pattern_service),
):
    """Pin a pattern so it won't be overwritten by nightly learning.

    Pinned patterns represent admin preferences that override data-driven learning.
    """
    try:
        return await svc.pin_pattern(pattern_id, body.user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Pattern not found")


@router.post(
    "/tenant/{tenant_id}/patterns/{pattern_id}/unpin",
    response_model=TenantPatternResponse,
    summary="Unpin a pattern",
)
async def unpin_pattern(
    tenant_id: uuid.UUID,
    pattern_id: uuid.UUID,
    svc: TenantPatternService = Depends(_get_pattern_service),
):
    """Unpin a pattern so nightly jobs can update it again."""
    try:
        return await svc.unpin_pattern(pattern_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Pattern not found")


# ── Blended recommendations ─────────────────────────────────────


def _get_blended_service(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> BlendedRecommendationService:
    return BlendedRecommendationService(db, tenant_id)


@router.get(
    "/tenant/{tenant_id}/blended-recommendations",
    response_model=BlendedRecommendationsResponse,
    summary="Get blended recommendations (tenant + global priors)",
)
async def get_blended_recommendations(
    tenant_id: uuid.UUID,
    category: str | None = Query(default=None, description="Filter by category"),
    svc: BlendedRecommendationService = Depends(_get_blended_service),
):
    """Get recommendations blending tenant-learned patterns with global priors.

    For cold-start tenants, returns global defaults clearly labeled.
    As tenant data grows, tenant-specific insights gradually take over.
    Each recommendation includes source, confidence, and blend weights.
    """
    result = await svc.get_blended_recommendations(category=category)
    return BlendedRecommendationsResponse(
        recommendations=[
            BlendedRecommendationResponse(**r) for r in result["recommendations"]
        ],
        data_status=DataMaturityStatus(**result["data_status"]),
    )


@router.get(
    "/tenant/{tenant_id}/onboarding-recommendations",
    summary="Get onboarding recommendations from global priors",
)
async def get_onboarding_recommendations(
    tenant_id: uuid.UUID,
    svc: BlendedRecommendationService = Depends(_get_blended_service),
):
    """Get recommendations for the onboarding flow.

    Returns global priors grouped by category with human-friendly labels.
    Designed for display during new tenant setup.
    """
    return await svc.get_onboarding_recommendations()


@router.post(
    "/tenant/{tenant_id}/migrate-cold-start",
    summary="Initialize cold-start recommendations for existing tenant",
)
async def migrate_cold_start(
    tenant_id: uuid.UUID,
    svc: BlendedRecommendationService = Depends(_get_blended_service),
):
    """Migrate an existing low-data tenant to use global priors.

    Safe to call multiple times — idempotent.
    Returns a summary of what was applied.
    """
    return await svc.migrate_cold_start_tenant()


# ── Backfill endpoints ───────────────────────────────────────────


@router.post(
    "/tenant/{tenant_id}/backfill",
    response_model=BackfillReportResponse,
    summary="Backfill learning data from historical posts",
)
async def backfill_tenant(
    tenant_id: uuid.UUID,
    body: BackfillRequest,
    db: AsyncSession = Depends(get_db),
):
    """Run historical backfill for a tenant.

    Extracts features, computes outcomes, emits learning events, and
    optionally bootstraps patterns from existing post data.
    Idempotent — safe to re-run. Resumable via cursor.
    """
    from amplify.learning.backfill import BackfillEngine

    engine = BackfillEngine(db, tenant_id, dry_run=body.dry_run)
    report = await engine.run(
        batch_size=body.batch_size,
        skip_features=body.skip_features,
        skip_outcomes=body.skip_outcomes,
        skip_events=body.skip_events,
        bootstrap_patterns=body.bootstrap_patterns,
        cursor=body.cursor,
    )
    return report.to_dict()

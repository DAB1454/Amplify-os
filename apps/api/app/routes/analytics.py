"""Analytics routes — overview, time-series, scores, and analyst reports."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id
from app.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _get_analytics_service(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> AnalyticsService:
    return AnalyticsService(db, tenant_id)


@router.get("/overview")
async def analytics_overview(
    svc: AnalyticsService = Depends(_get_analytics_service),
):
    """High-level analytics overview for the tenant."""
    return await svc.get_overview()


@router.get("/timeseries")
async def all_timeseries(
    days: int = Query(default=30, ge=1, le=365),
    svc: AnalyticsService = Depends(_get_analytics_service),
):
    """Daily impressions, engagement, and clicks across all campaigns."""
    return await svc.get_campaign_timeseries(None, days=days)


@router.get("/campaigns/{campaign_id}/timeseries")
async def campaign_timeseries(
    campaign_id: uuid.UUID,
    days: int = Query(default=30, ge=1, le=365),
    svc: AnalyticsService = Depends(_get_analytics_service),
):
    """Daily impressions, engagement, and clicks for a campaign."""
    return await svc.get_campaign_timeseries(campaign_id, days=days)


@router.get("/scores")
async def post_scores(
    campaign_id: uuid.UUID | None = Query(default=None),
    days: int = Query(default=14, ge=1, le=90),
    svc: AnalyticsService = Depends(_get_analytics_service),
):
    """Score all posts by normalized engagement and CTR."""
    return await svc.get_post_scores(campaign_id=campaign_id, days=days)


@router.get("/analyst-report")
async def analyst_report(
    campaign_id: uuid.UUID | None = Query(default=None),
    days: int = Query(default=7, ge=1, le=90),
    svc: AnalyticsService = Depends(_get_analytics_service),
):
    """Generate weekly keep/remix/stop analyst report."""
    return await svc.generate_analyst_report(campaign_id=campaign_id, days=days)


@router.get("/experiments/{experiment_id}")
async def experiment_results(
    experiment_id: uuid.UUID,
    svc: AnalyticsService = Depends(_get_analytics_service),
):
    """Get scoring results for an experiment's variants."""
    try:
        return await svc.get_experiment_results(experiment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/demo")
async def demo_dataset():
    """Return the full mock demo dataset for development/testing."""
    from amplify.core.analytics.mock_data import generate_demo_dataset
    return generate_demo_dataset()

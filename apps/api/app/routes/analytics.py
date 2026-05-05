"""Analytics routes — overview, time-series, scores, analyst reports, and experiments."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id
from app.services.analytics_service import AnalyticsService
from amplify.db.models.experiment import ExperimentModel

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _get_analytics_service(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> AnalyticsService:
    return AnalyticsService(db, tenant_id)


@router.get("/overview")
async def analytics_overview(
    days: int | None = Query(default=None, ge=1, le=365),
    svc: AnalyticsService = Depends(_get_analytics_service),
):
    """High-level analytics overview for the tenant.

    Pass ``days`` to scope counts and engagement totals to a window;
    omit it for all-time totals.
    """
    return await svc.get_overview(days=days)


@router.get("/timeseries")
async def all_timeseries(
    days: int = Query(default=30, ge=1, le=365),
    platform: str | None = Query(default=None),
    svc: AnalyticsService = Depends(_get_analytics_service),
):
    """Daily impressions, engagement, and clicks across all campaigns."""
    return await svc.get_campaign_timeseries(None, days=days, platform=platform)


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


# ── Experiment CRUD ──────────────────────────────────────────────


class ExperimentVariant(BaseModel):
    name: str
    post_ids: list[str] = []


class ExperimentCreateRequest(BaseModel):
    campaign_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=255)
    hypothesis: str = ""
    variants: list[ExperimentVariant] = Field(..., min_length=2)
    start_date: date | None = None
    end_date: date | None = None


class ExperimentUpdateRequest(BaseModel):
    name: str | None = None
    hypothesis: str | None = None
    variants: list[ExperimentVariant] | None = None
    start_date: date | None = None
    end_date: date | None = None


class ExperimentResponse(BaseModel):
    id: str
    tenant_id: str
    campaign_id: str
    name: str
    hypothesis: str
    status: str
    variants: list[dict[str, Any]]
    winner_variant: int | None
    outcome: dict[str, Any] | None
    start_date: date | None
    end_date: date | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime | None


@router.get("/experiments", response_model=list[ExperimentResponse])
async def list_experiments(
    campaign_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """List all experiments for the tenant."""
    stmt = select(ExperimentModel).where(ExperimentModel.tenant_id == tenant_id)
    if campaign_id:
        stmt = stmt.where(ExperimentModel.campaign_id == campaign_id)
    stmt = stmt.order_by(ExperimentModel.created_at.desc())
    result = await db.execute(stmt)
    experiments = result.scalars().all()
    return [_experiment_to_dict(e) for e in experiments]


@router.post("/experiments", response_model=ExperimentResponse, status_code=status.HTTP_201_CREATED)
async def create_experiment(
    body: ExperimentCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Create a new A/B experiment."""
    experiment = ExperimentModel(
        tenant_id=tenant_id,
        campaign_id=body.campaign_id,
        name=body.name,
        hypothesis=body.hypothesis,
        status="draft",
        variants=[v.model_dump() for v in body.variants],
        start_date=body.start_date,
        end_date=body.end_date,
    )
    db.add(experiment)
    await db.flush()
    return _experiment_to_dict(experiment)


@router.put("/experiments/{experiment_id}", response_model=ExperimentResponse)
async def update_experiment(
    experiment_id: uuid.UUID,
    body: ExperimentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Update an experiment (only draft/running)."""
    experiment = await _get_experiment(db, experiment_id, tenant_id)
    if experiment.status == "completed":
        raise HTTPException(status_code=400, detail="Cannot update a completed experiment")

    if body.name is not None:
        experiment.name = body.name
    if body.hypothesis is not None:
        experiment.hypothesis = body.hypothesis
    if body.variants is not None:
        experiment.variants = [v.model_dump() for v in body.variants]
    if body.start_date is not None:
        experiment.start_date = body.start_date
    if body.end_date is not None:
        experiment.end_date = body.end_date

    await db.flush()
    return _experiment_to_dict(experiment)


@router.post("/experiments/{experiment_id}/run")
async def run_experiment(
    experiment_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Trigger the experiment evaluation loop."""
    experiment = await _get_experiment(db, experiment_id, tenant_id)
    if experiment.status == "completed":
        raise HTTPException(status_code=400, detail="Experiment already completed")

    try:
        from worker.app.queue import JobQueue
        r = request.app.state.redis
        q = JobQueue(r)
        await q.enqueue(
            "run_experiment_loop",
            {"experiment_id": str(experiment_id), "tenant_id": str(tenant_id)},
            idempotency_key=f"experiment:{experiment_id}",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to enqueue: {exc}")

    return {"status": "queued", "experiment_id": str(experiment_id)}


@router.delete("/experiments/{experiment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_experiment(
    experiment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Delete an experiment."""
    experiment = await _get_experiment(db, experiment_id, tenant_id)
    await db.delete(experiment)
    await db.flush()


async def _get_experiment(
    db: AsyncSession, experiment_id: uuid.UUID, tenant_id: uuid.UUID
) -> ExperimentModel:
    result = await db.execute(
        select(ExperimentModel).where(
            ExperimentModel.id == experiment_id,
            ExperimentModel.tenant_id == tenant_id,
        )
    )
    experiment = result.scalar_one_or_none()
    if experiment is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    return experiment


def _experiment_to_dict(e: ExperimentModel) -> dict:
    return {
        "id": str(e.id),
        "tenant_id": str(e.tenant_id),
        "campaign_id": str(e.campaign_id),
        "name": e.name,
        "hypothesis": e.hypothesis,
        "status": e.status,
        "variants": e.variants or [],
        "winner_variant": e.winner_variant,
        "outcome": e.outcome,
        "start_date": e.start_date,
        "end_date": e.end_date,
        "started_at": e.started_at,
        "completed_at": e.completed_at,
        "created_at": e.created_at,
    }

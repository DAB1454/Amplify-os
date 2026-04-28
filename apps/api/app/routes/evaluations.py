"""Evaluation run management and reporting routes."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id
from app.schemas.learning import EvaluationRunResponse, EvaluationResultResponse
from app.services.evaluation_service import EvaluationService

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


# ── Request schemas ──────────────────────────────────────────────


class EvaluationRunCreate(BaseModel):
    """Request body for triggering an evaluation run."""
    evaluation_type: str = Field(default="ranking", max_length=50)
    scope: str = Field(default="tenant", max_length=50)
    window_start: datetime | None = None
    window_end: datetime | None = None
    top_k: int = Field(default=3, ge=1, le=20)
    use_sample_dataset: bool = False


class EvaluationDashboard(BaseModel):
    """Dashboard summary for evaluation runs."""
    total_runs: int
    latest_run: EvaluationRunResponse | None
    comparison: dict | None


# ── Dependencies ─────────────────────────────────────────────────


def _get_service(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> EvaluationService:
    return EvaluationService(db, tenant_id)


# ── Routes ───────────────────────────────────────────────────────


@router.post(
    "/runs",
    response_model=EvaluationRunResponse,
    status_code=201,
    summary="Trigger a new evaluation run",
)
async def create_run(
    body: EvaluationRunCreate,
    svc: EvaluationService = Depends(_get_service),
):
    """Trigger an offline replay evaluation.

    If use_sample_dataset is True, uses the built-in 12-post sample dataset
    instead of building one from stored feature vectors.
    """
    dataset = None
    if body.use_sample_dataset:
        from amplify.learning.evaluation.replay import build_sample_dataset
        dataset = build_sample_dataset()

    # Build a default ranking policy from tenant patterns
    from amplify.learning.ranking.post_ranker import PostRanker
    from amplify.learning.evaluation.replay import RankingPolicySpec

    policies = [
        RankingPolicySpec(
            name="default",
            ranker=PostRanker(),
            description="Default ranking policy (no tenant patterns)",
        ),
    ]

    run = await svc.run_evaluation(
        policies=policies,
        dataset=dataset,
        evaluation_type=body.evaluation_type,
        scope=body.scope,
        window_start=body.window_start,
        window_end=body.window_end,
        top_k=body.top_k,
    )
    return run


@router.get(
    "/runs",
    response_model=list[EvaluationRunResponse],
    summary="List evaluation runs",
)
async def list_runs(
    evaluation_type: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    svc: EvaluationService = Depends(_get_service),
):
    return await svc.list_runs(evaluation_type, limit, offset)


@router.get(
    "/runs/{run_id}",
    response_model=EvaluationRunResponse,
    summary="Get a specific evaluation run",
)
async def get_run(
    run_id: uuid.UUID,
    svc: EvaluationService = Depends(_get_service),
):
    run = await svc.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    return run


@router.get(
    "/runs/{run_id}/results",
    response_model=list[EvaluationResultResponse],
    summary="Get per-post results for an evaluation run",
)
async def get_results(
    run_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: EvaluationService = Depends(_get_service),
):
    return await svc.get_results(run_id, limit, offset)


@router.get(
    "/runs/{run_id}/comparison",
    summary="Get full comparison report for an evaluation run",
)
async def get_comparison(
    run_id: uuid.UUID,
    svc: EvaluationService = Depends(_get_service),
):
    comparison = await svc.get_comparison(run_id)
    if not comparison:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    return comparison


@router.get(
    "/dashboard",
    response_model=EvaluationDashboard,
    summary="Get evaluation dashboard summary",
)
async def get_dashboard(
    svc: EvaluationService = Depends(_get_service),
):
    """Returns the latest run and its comparison report for a quick dashboard view."""
    runs = await svc.list_runs(limit=1)
    latest = runs[0] if runs else None
    comparison = None
    if latest:
        comparison = await svc.get_comparison(latest.id)

    all_runs = await svc.list_runs(limit=1000)
    return EvaluationDashboard(
        total_runs=len(all_runs),
        latest_run=latest,
        comparison=comparison,
    )

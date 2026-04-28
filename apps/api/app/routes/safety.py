"""Exploration controls and safety rails routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id, get_user_id
from app.middleware.rbac import require_role
from app.services.safety_service import SafetyService

router = APIRouter(prefix="/safety", tags=["safety"])


# ── Request/response schemas ─────────────────────────────────────


class ExplorationControlsResponse(BaseModel):
    enabled: bool
    max_exploration_rate: float
    confidence_threshold: float
    min_sample_size: int
    quiet_hours_start: int | None
    quiet_hours_end: int | None
    max_experiments_per_week: int
    channel_limits: dict[str, int]
    rollback_threshold: float
    baseline_window: int
    require_approval_for_experiments: bool
    shadow_only: bool


class ExplorationControlsUpdate(BaseModel):
    enabled: bool | None = None
    max_exploration_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    min_sample_size: int | None = Field(default=None, ge=0)
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)
    max_experiments_per_week: int | None = Field(default=None, ge=0)
    channel_limits: dict[str, int] | None = None
    rollback_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    baseline_window: int | None = Field(default=None, ge=1)
    require_approval_for_experiments: bool | None = None
    shadow_only: bool | None = None


class SafetyCheckRequest(BaseModel):
    platform: str = ""
    requested_epsilon: float = Field(default=0.1, ge=0.0, le=1.0)
    total_observations: int | None = None
    rank_correlation: float | None = None
    pending_approval: bool = False


class SafetyCheckResponse(BaseModel):
    allowed: bool
    reason: str
    force_shadow: bool
    force_fallback: bool
    capped_epsilon: float | None
    alerts: list[dict]


class RollbackResponse(BaseModel):
    should_rollback: bool
    reason: str
    baseline_reward: float | None
    current_reward: float | None
    drop_fraction: float | None
    threshold: float | None
    alerts: list[dict]


class ExperimentStatsResponse(BaseModel):
    controls: dict
    experiments_this_week: int
    total_observations: int
    platform_usage: dict[str, int]
    budget_remaining: int


# ── Dependencies ─────────────────────────────────────────────────


def _get_service(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> SafetyService:
    return SafetyService(db, tenant_id)


# ── Routes ───────────────────────────────────────────────────────


@router.get(
    "/controls",
    response_model=ExplorationControlsResponse,
    summary="Get exploration controls for this tenant",
)
async def get_controls(
    svc: SafetyService = Depends(_get_service),
):
    controls = await svc.get_controls()
    return controls.to_dict()


@router.put(
    "/controls",
    response_model=ExplorationControlsResponse,
    summary="Update exploration controls",
    dependencies=[Depends(require_role("admin"))],
)
async def update_controls(
    body: ExplorationControlsUpdate,
    svc: SafetyService = Depends(_get_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    try:
        controls = await svc.update_controls(updates, user_id)
        return controls.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post(
    "/check",
    response_model=SafetyCheckResponse,
    summary="Run a pre-exploration safety check",
)
async def check_exploration(
    body: SafetyCheckRequest,
    svc: SafetyService = Depends(_get_service),
):
    result = await svc.check_before_exploration(
        platform=body.platform,
        requested_epsilon=body.requested_epsilon,
        total_observations=body.total_observations,
        rank_correlation=body.rank_correlation,
        pending_approval=body.pending_approval,
    )
    return result.to_dict()


@router.post(
    "/rollback-check",
    response_model=RollbackResponse,
    summary="Evaluate whether auto-rollback should trigger",
)
async def check_rollback(
    svc: SafetyService = Depends(_get_service),
):
    decision = await svc.evaluate_rollback()
    return decision.to_dict()


@router.get(
    "/anomalies",
    summary="Detect anomalous learning behavior",
)
async def detect_anomalies(
    svc: SafetyService = Depends(_get_service),
):
    alerts = await svc.detect_anomalies()
    return {"alerts": [a.to_dict() for a in alerts]}


@router.get(
    "/stats",
    response_model=ExperimentStatsResponse,
    summary="Get experiment usage stats",
)
async def get_stats(
    svc: SafetyService = Depends(_get_service),
):
    return await svc.get_experiment_stats()

"""Outcome recording and reward query routes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id
from app.schemas.learning import PostOutcomeResponse
from app.services.outcome_service import OutcomeService

router = APIRouter(prefix="/outcomes", tags=["outcomes"])


# ── Request schemas ──────────────────────────────────────────────


class RecordOutcomeRequest(BaseModel):
    post_id: uuid.UUID
    measurement_window: str = Field(max_length=20)
    metrics: dict[str, Any]
    measured_at: datetime | None = None
    source: str = ""


class RecomputeRequest(BaseModel):
    weight_overrides: dict[str, float] | None = None


# ── Dependencies ─────────────────────────────────────────────────


def _get_outcome_service(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> OutcomeService:
    return OutcomeService(db, tenant_id)


# ── Endpoints ────────────────────────────────────────────────────


@router.post("", response_model=PostOutcomeResponse, status_code=201)
@router.post("/", response_model=PostOutcomeResponse, status_code=201)
async def record_outcome(
    body: RecordOutcomeRequest,
    svc: OutcomeService = Depends(_get_outcome_service),
):
    """Record outcome metrics for a post and compute reward."""
    outcome = await svc.record_outcome(
        post_id=body.post_id,
        measurement_window=body.measurement_window,
        metrics=body.metrics,
        measured_at=body.measured_at,
        source=body.source,
    )
    return outcome


@router.get("/{post_id}", response_model=list[PostOutcomeResponse])
async def get_outcomes(
    post_id: uuid.UUID,
    svc: OutcomeService = Depends(_get_outcome_service),
):
    """Get all outcome measurements for a post."""
    return await svc.get_outcomes(post_id)


@router.get("/{post_id}/latest", response_model=PostOutcomeResponse)
async def get_latest_outcome(
    post_id: uuid.UUID,
    svc: OutcomeService = Depends(_get_outcome_service),
):
    """Get the most recent outcome measurement for a post."""
    outcome = await svc.get_latest_outcome(post_id)
    if outcome is None:
        raise HTTPException(status_code=404, detail="No outcomes found")
    return outcome


@router.post("/{post_id}/recompute", response_model=list[PostOutcomeResponse])
async def recompute_rewards(
    post_id: uuid.UUID,
    body: RecomputeRequest | None = None,
    svc: OutcomeService = Depends(_get_outcome_service),
):
    """Recompute rewards for all outcome windows of a post."""
    overrides = body.weight_overrides if body else None
    outcomes = await svc.recompute_rewards(post_id, weight_overrides=overrides)
    if not outcomes:
        raise HTTPException(status_code=404, detail="No outcomes found")
    return outcomes

"""Feature extraction API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id
from app.schemas.learning import PostFeatureVectorResponse
from app.services.feature_extraction_service import FeatureExtractionService

router = APIRouter(prefix="/features", tags=["features"])


# ── Dependencies ─────────────────────────────────────────────────


def _get_feature_service(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> FeatureExtractionService:
    return FeatureExtractionService(db, tenant_id)


# ── Endpoints ────────────────────────────────────────────────────


@router.post(
    "/extract/{post_id}",
    response_model=PostFeatureVectorResponse,
    status_code=status.HTTP_201_CREATED,
)
async def extract_post_features(
    post_id: uuid.UUID,
    svc: FeatureExtractionService = Depends(_get_feature_service),
):
    """Extract and persist features for a single post."""
    try:
        vector = await svc.extract_and_persist(post_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return vector


@router.get("/{post_id}", response_model=PostFeatureVectorResponse)
async def get_post_features(
    post_id: uuid.UUID,
    svc: FeatureExtractionService = Depends(_get_feature_service),
):
    """Get the feature vector for a post."""
    vector = await svc.get_vector(post_id)
    if vector is None:
        raise HTTPException(status_code=404, detail="Feature vector not found")
    return vector


@router.post("/backfill")
async def backfill_features(
    limit: int = Query(default=500, le=5000),
    svc: FeatureExtractionService = Depends(_get_feature_service),
):
    """Backfill feature vectors for posts that don't have one yet."""
    result = await svc.backfill(limit=limit)
    return result

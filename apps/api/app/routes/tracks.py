"""Track CRUD routes — nested under releases."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id
from app.schemas import TrackCreateRequest, TrackUpdateRequest, TrackResponse
from amplify.db.models.track import TrackModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/releases/{release_id}/tracks", tags=["tracks"])


@router.get("", response_model=list[TrackResponse])
@router.get("/", response_model=list[TrackResponse])
async def list_tracks(
    release_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """List all tracks for a release, ordered by track number."""
    result = await db.execute(
        select(TrackModel)
        .where(TrackModel.release_id == release_id, TrackModel.tenant_id == tenant_id)
        .order_by(TrackModel.track_number)
    )
    return result.scalars().all()


@router.post("", response_model=TrackResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=TrackResponse, status_code=status.HTTP_201_CREATED)
async def create_track(
    release_id: uuid.UUID,
    body: TrackCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Add a track to a release."""
    track = TrackModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        release_id=release_id,
        **body.model_dump(),
    )
    db.add(track)
    await db.flush()
    await db.refresh(track)
    await db.commit()
    return track


@router.put("/{track_id}", response_model=TrackResponse)
async def update_track(
    release_id: uuid.UUID,
    track_id: uuid.UUID,
    body: TrackUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Update a track."""
    result = await db.execute(
        select(TrackModel).where(
            TrackModel.id == track_id,
            TrackModel.release_id == release_id,
            TrackModel.tenant_id == tenant_id,
        )
    )
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(track, key, value)

    await db.flush()
    await db.refresh(track)
    await db.commit()
    return track


@router.delete("/{track_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_track(
    release_id: uuid.UUID,
    track_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Delete a track from a release."""
    result = await db.execute(
        select(TrackModel).where(
            TrackModel.id == track_id,
            TrackModel.release_id == release_id,
            TrackModel.tenant_id == tenant_id,
        )
    )
    track = result.scalar_one_or_none()
    if not track:
        raise HTTPException(status_code=404, detail="Track not found")

    await db.delete(track)
    await db.commit()

"""Track CRUD routes — nested under releases."""

from __future__ import annotations

import csv
import io
import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id
from app.schemas import TrackCreateRequest, TrackUpdateRequest, TrackResponse
from amplify.db.models.track import TrackModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/releases/{release_id}/tracks", tags=["tracks"])


class BulkTrackItem(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    track_number: int = 1
    duration_seconds: int | None = None
    isrc: str | None = None
    audio_url: str | None = None
    lyrics: str | None = None
    is_single: bool = False


class BulkCreateRequest(BaseModel):
    tracks: list[BulkTrackItem]


class BulkCreateResponse(BaseModel):
    created: int
    tracks: list[TrackResponse]


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


@router.post("/bulk", response_model=BulkCreateResponse, status_code=status.HTTP_201_CREATED)
async def bulk_create_tracks(
    release_id: uuid.UUID,
    body: BulkCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Create multiple tracks at once for a release."""
    created = []
    for item in body.tracks:
        track = TrackModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            release_id=release_id,
            **item.model_dump(),
        )
        db.add(track)
        created.append(track)

    await db.flush()
    for t in created:
        await db.refresh(t)
    await db.commit()

    return BulkCreateResponse(created=len(created), tracks=created)


class CSVImportResponse(BaseModel):
    created: int
    skipped: int
    errors: list[str]
    tracks: list[TrackResponse]


@router.post("/import-csv", response_model=CSVImportResponse, status_code=status.HTTP_201_CREATED)
async def import_tracks_csv(
    release_id: uuid.UUID,
    file: UploadFile = File(..., description="CSV file with track data"),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Import tracks from a CSV file.

    Expected columns (header row required):
      title (required), track_number, duration_seconds, isrc, is_single
    Unknown columns are ignored.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv")

    contents = await file.read()
    try:
        text = contents.decode("utf-8-sig")  # Handle BOM from Excel
    except UnicodeDecodeError:
        text = contents.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "title" not in [f.strip().lower() for f in reader.fieldnames]:
        raise HTTPException(status_code=400, detail="CSV must have a 'title' column")

    created = []
    skipped = 0
    errors = []

    for row_num, row in enumerate(reader, start=2):
        # Normalize keys to lowercase
        row = {k.strip().lower(): v.strip() for k, v in row.items() if k and v}

        title = row.get("title", "").strip()
        if not title:
            skipped += 1
            errors.append(f"Row {row_num}: missing title — skipped")
            continue

        track_number = 1
        if "track_number" in row:
            try:
                track_number = int(row["track_number"])
            except ValueError:
                track_number = len(created) + 1

        duration = None
        if "duration_seconds" in row:
            try:
                duration = int(row["duration_seconds"])
            except ValueError:
                pass
        elif "duration" in row:
            # Support mm:ss format
            dur_str = row["duration"]
            if ":" in dur_str:
                parts = dur_str.split(":")
                try:
                    duration = int(parts[0]) * 60 + int(parts[1])
                except ValueError:
                    pass
            else:
                try:
                    duration = int(dur_str)
                except ValueError:
                    pass

        is_single = row.get("is_single", "").lower() in ("true", "1", "yes")

        track = TrackModel(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            release_id=release_id,
            title=title,
            track_number=track_number,
            duration_seconds=duration,
            isrc=row.get("isrc") or None,
            audio_url=row.get("audio_url") or None,
            lyrics=row.get("lyrics") or None,
            is_single=is_single,
        )
        db.add(track)
        created.append(track)

    if created:
        await db.flush()
        for t in created:
            await db.refresh(t)
        await db.commit()

    return CSVImportResponse(
        created=len(created),
        skipped=skipped,
        errors=errors,
        tracks=created,
    )

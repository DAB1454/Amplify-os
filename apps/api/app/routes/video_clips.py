"""Video clip library routes — upload music videos, manage extracted clips."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.deps import get_db, get_settings, get_tenant_id
from app.services.media_service import MediaService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/video-clips", tags=["video-clips"])


def _get_media_service(settings: Settings = Depends(get_settings)) -> MediaService:
    return MediaService(
        s3_bucket=settings.s3_bucket,
        s3_region=settings.s3_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        media_base_url=settings.media_base_url,
    )


# ── Schemas ──────────────────────────────────────────────────────

class VideoUploadResponse(BaseModel):
    asset_id: str
    analysis_job_id: str
    status: str = "processing"


class ClipResponse(BaseModel):
    id: str
    source_asset_id: str
    track_id: str | None = None
    release_id: str | None = None
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    clip_url_vertical: str | None = None
    clip_url_landscape: str | None = None
    clip_url_square: str | None = None
    thumbnail_url: str | None = None
    clip_type: str
    energy_score: float
    ai_description: str
    ai_tags: list[str]
    uses_count: int
    status: str
    created_at: str | None = None

    class Config:
        from_attributes = True


class AnalysisJobResponse(BaseModel):
    id: str
    source_asset_id: str = ""
    source_asset_name: str = ""
    track_id: str | None = None
    status: str
    clips_detected: int
    clips_extracted: int
    error_message: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    created_at: str | None = None


# ── Upload music video ───────────────────────────────────────────

@router.post("/upload-video", response_model=VideoUploadResponse)
async def upload_music_video(
    request: Request,
    file: UploadFile = File(...),
    track_id: uuid.UUID | None = Query(None),
    release_id: uuid.UUID | None = Query(None),
    artist_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    media_service: MediaService = Depends(_get_media_service),
    settings: Settings = Depends(get_settings),
):
    """Upload a music video and start clip analysis."""
    from amplify.db.models.asset import AssetModel
    from amplify.db.models.video_clip import VideoClipAnalysisJobModel

    if not file.content_type or not file.content_type.startswith("video/"):
        raise HTTPException(400, "File must be a video")

    # Upload to S3
    file_url = await media_service.upload(
        tenant_id, file.file, file.filename or "video.mp4", file.content_type,
    )

    # Create asset record
    asset = AssetModel(
        tenant_id=tenant_id,
        artist_id=artist_id,
        release_id=release_id,
        asset_type="music_video",
        name=file.filename or "Music Video",
        file_url=file_url,
        mime_type=file.content_type,
        source="uploaded",
        approval_status="approved",
    )
    db.add(asset)
    await db.flush()

    # Create analysis job
    job = VideoClipAnalysisJobModel(
        tenant_id=tenant_id,
        source_asset_id=asset.id,
        track_id=track_id,
    )
    db.add(job)
    await db.flush()

    # Enqueue worker job via Redis directly (worker module not on API PYTHONPATH)
    redis_client = getattr(request.app.state, "redis", None)
    if redis_client:
        import json as _json
        from datetime import datetime as _dt, timezone as _tz
        _job_id = uuid.uuid4().hex
        envelope = _json.dumps({
            "job_id": _job_id,
            "job_type": "analyze_video_clips",
            "payload": {
                "source_asset_id": str(asset.id),
                "track_id": str(track_id) if track_id else None,
                "release_id": str(release_id) if release_id else None,
                "artist_id": str(artist_id) if artist_id else None,
                "tenant_id": str(tenant_id),
                "job_id": str(job.id),
            },
            "idempotency_key": "",
            "enqueued_at": _dt.now(_tz.utc).isoformat(),
            "max_retries": 2,
            "attempt": 0,
            "backoff_base": 2,
            "timeout_seconds": 900,
        })
        await redis_client.rpush("amplify:queue", envelope)
        logger.info("Enqueued analyze_video_clips job %s for asset %s", job.id, asset.id)
    else:
        logger.warning("Redis not available — clip analysis job not enqueued")

    return VideoUploadResponse(
        asset_id=str(asset.id),
        analysis_job_id=str(job.id),
    )


# ── Analysis job status ──────────────────────────────────────────

def _job_to_response(job, asset_name: str = "") -> AnalysisJobResponse:
    return AnalysisJobResponse(
        id=str(job.id),
        source_asset_id=str(job.source_asset_id),
        source_asset_name=asset_name,
        track_id=str(job.track_id) if job.track_id else None,
        status=job.status,
        clips_detected=job.clips_detected,
        clips_extracted=job.clips_extracted,
        error_message=job.error_message,
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        created_at=job.created_at.isoformat() if job.created_at else None,
    )


@router.get("/analysis-jobs", response_model=list[AnalysisJobResponse])
async def list_analysis_jobs(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    track_id: uuid.UUID | None = Query(None),
    release_id: uuid.UUID | None = Query(None),
):
    """List video analysis jobs, optionally filtered by track or release."""
    from amplify.db.models.video_clip import VideoClipAnalysisJobModel
    from amplify.db.models.asset import AssetModel

    q = select(VideoClipAnalysisJobModel).where(
        VideoClipAnalysisJobModel.tenant_id == tenant_id,
    )
    if track_id:
        q = q.where(VideoClipAnalysisJobModel.track_id == track_id)
    q = q.order_by(VideoClipAnalysisJobModel.created_at.desc()).limit(50)
    result = await db.execute(q)
    jobs = result.scalars().all()

    # Batch-fetch asset names
    asset_ids = {j.source_asset_id for j in jobs}
    asset_names: dict[uuid.UUID, str] = {}
    if asset_ids:
        asset_result = await db.execute(
            select(AssetModel.id, AssetModel.name).where(AssetModel.id.in_(asset_ids))
        )
        asset_names = {row.id: row.name for row in asset_result.all()}

    return [_job_to_response(j, asset_names.get(j.source_asset_id, "")) for j in jobs]


@router.get("/analysis/{job_id}", response_model=AnalysisJobResponse)
async def get_analysis_status(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Poll clip analysis job status."""
    from amplify.db.models.video_clip import VideoClipAnalysisJobModel
    from amplify.db.models.asset import AssetModel

    result = await db.execute(
        select(VideoClipAnalysisJobModel).where(
            VideoClipAnalysisJobModel.id == job_id,
            VideoClipAnalysisJobModel.tenant_id == tenant_id,
        )
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Analysis job not found")

    asset_result = await db.execute(
        select(AssetModel.name).where(AssetModel.id == job.source_asset_id)
    )
    asset_name = asset_result.scalar_one_or_none() or ""

    return _job_to_response(job, asset_name)


# ── List clips ───────────────────────────────────────────────────

@router.get("", response_model=list[ClipResponse])
@router.get("/", response_model=list[ClipResponse])
async def list_clips(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    track_id: uuid.UUID | None = Query(None),
    release_id: uuid.UUID | None = Query(None),
    clip_type: str | None = Query(None),
    status: str | None = Query(None, description="Filter by status: ready, processing, etc."),
):
    """List video clips with optional filters."""
    from amplify.db.models.video_clip import VideoClipModel

    q = select(VideoClipModel).where(
        VideoClipModel.tenant_id == tenant_id,
    )
    if track_id:
        q = q.where(VideoClipModel.track_id == track_id)
    if release_id:
        q = q.where(VideoClipModel.release_id == release_id)
    if clip_type:
        q = q.where(VideoClipModel.clip_type == clip_type)
    if status:
        q = q.where(VideoClipModel.status == status)

    q = q.order_by(VideoClipModel.energy_score.desc()).limit(50)
    result = await db.execute(q)
    clips = result.scalars().all()

    return [
        ClipResponse(
            id=str(c.id),
            source_asset_id=str(c.source_asset_id),
            track_id=str(c.track_id) if c.track_id else None,
            release_id=str(c.release_id) if c.release_id else None,
            start_seconds=c.start_seconds,
            end_seconds=c.end_seconds,
            duration_seconds=c.duration_seconds,
            clip_url_vertical=c.clip_url_vertical,
            clip_url_landscape=c.clip_url_landscape,
            clip_url_square=c.clip_url_square,
            thumbnail_url=c.thumbnail_url,
            clip_type=c.clip_type,
            energy_score=c.energy_score,
            ai_description=c.ai_description,
            ai_tags=c.ai_tags or [],
            uses_count=c.uses_count,
            status=c.status,
            created_at=c.created_at.isoformat() if c.created_at else None,
        )
        for c in clips
    ]


# ── Get single clip ──────────────────────────────────────────────

@router.get("/{clip_id}", response_model=ClipResponse)
async def get_clip(
    clip_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Get details for a single clip."""
    from amplify.db.models.video_clip import VideoClipModel

    result = await db.execute(
        select(VideoClipModel).where(
            VideoClipModel.id == clip_id,
            VideoClipModel.tenant_id == tenant_id,
        )
    )
    clip = result.scalar_one_or_none()
    if not clip:
        raise HTTPException(404, "Clip not found")

    return ClipResponse(
        id=str(clip.id),
        source_asset_id=str(clip.source_asset_id),
        track_id=str(clip.track_id) if clip.track_id else None,
        release_id=str(clip.release_id) if clip.release_id else None,
        start_seconds=clip.start_seconds,
        end_seconds=clip.end_seconds,
        duration_seconds=clip.duration_seconds,
        clip_url_vertical=clip.clip_url_vertical,
        clip_url_landscape=clip.clip_url_landscape,
        clip_url_square=clip.clip_url_square,
        thumbnail_url=clip.thumbnail_url,
        clip_type=clip.clip_type,
        energy_score=clip.energy_score,
        ai_description=clip.ai_description,
        ai_tags=clip.ai_tags or [],
        uses_count=clip.uses_count,
        status=clip.status,
        created_at=clip.created_at.isoformat() if clip.created_at else None,
    )


# ── Delete / archive clip ────────────────────────────────────────

@router.delete("/{clip_id}")
async def delete_clip(
    clip_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Archive (soft-delete) a clip."""
    from amplify.db.models.video_clip import VideoClipModel

    result = await db.execute(
        select(VideoClipModel).where(
            VideoClipModel.id == clip_id,
            VideoClipModel.tenant_id == tenant_id,
        )
    )
    clip = result.scalar_one_or_none()
    if not clip:
        raise HTTPException(404, "Clip not found")

    clip.status = "archived"
    return {"status": "archived", "clip_id": str(clip_id)}

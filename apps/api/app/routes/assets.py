"""Asset library routes — upload, browse, tag, and manage media assets."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.deps import get_db, get_settings, get_tenant_id
from app.schemas import AssetCreateRequest, AssetResponse, AssetUpdateRequest
from app.services.media_service import ALLOWED_TYPES, MAX_FILE_SIZE, MediaService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assets", tags=["assets"])


def _get_media_service(settings: Settings = Depends(get_settings)) -> MediaService:
    return MediaService(
        s3_bucket=settings.s3_bucket,
        s3_region=settings.s3_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        media_base_url=settings.media_base_url,
    )


@router.get("", response_model=list[AssetResponse])
@router.get("/", response_model=list[AssetResponse])
async def list_assets(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    asset_type: str | None = Query(None, description="Filter by type: image, video, audio, etc."),
    artist_id: uuid.UUID | None = Query(None),
    release_id: uuid.UUID | None = Query(None),
    campaign_id: uuid.UUID | None = Query(None),
    tag: str | None = Query(None, description="Filter by tag"),
    source: str | None = Query(None, description="Filter by source: uploaded, ai_generated"),
):
    """List assets in the tenant's library with optional filters."""
    from amplify.db.models.asset import AssetModel

    q = select(AssetModel).where(AssetModel.tenant_id == tenant_id)

    if asset_type:
        q = q.where(AssetModel.asset_type == asset_type)
    if artist_id:
        q = q.where(AssetModel.artist_id == artist_id)
    if release_id:
        q = q.where(AssetModel.release_id == release_id)
    if campaign_id:
        q = q.where(AssetModel.campaign_id == campaign_id)
    if tag:
        q = q.where(AssetModel.tags.any(tag))
    if source:
        q = q.where(AssetModel.source == source)

    q = q.order_by(AssetModel.created_at.desc())
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/upload", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
async def upload_asset(
    file: UploadFile = File(...),
    name: str = Query("", description="Asset name (defaults to filename)"),
    asset_type: str = Query("image", description="Asset type: image, video, audio, album_art, promo_photo, logo"),
    description: str = Query(""),
    artist_id: uuid.UUID | None = Query(None),
    release_id: uuid.UUID | None = Query(None),
    campaign_id: uuid.UUID | None = Query(None),
    tags: str = Query("", description="Comma-separated tags"),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    svc: MediaService = Depends(_get_media_service),
):
    """Upload a file directly into the asset library."""
    from amplify.db.models.asset import AssetModel

    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail=f"File type '{content_type}' not allowed.")

    # Stream upload
    import tempfile
    tmp = tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024)
    total_size = 0

    try:
        while True:
            chunk = await file.read(256 * 1024)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > MAX_FILE_SIZE:
                tmp.close()
                raise HTTPException(status_code=400, detail=f"File too large (>{MAX_FILE_SIZE // (1024*1024)} MB).")
            tmp.write(chunk)

        tmp.seek(0)
        url = await svc.upload(tenant_id, tmp, file.filename, content_type)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Asset upload failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}")
    finally:
        tmp.close()

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    asset = AssetModel(
        tenant_id=tenant_id,
        artist_id=artist_id,
        release_id=release_id,
        campaign_id=campaign_id,
        asset_type=asset_type,
        name=name or file.filename,
        description=description,
        file_url=url,
        file_size_bytes=total_size,
        mime_type=content_type,
        tags=tag_list,
        source="uploaded",
    )
    db.add(asset)
    await db.flush()
    await db.refresh(asset)
    return asset


@router.post("/", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
async def create_asset_from_url(
    body: AssetCreateRequest,
    file_url: str = Query(..., description="URL of existing file (e.g. S3 URL)"),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Register an existing URL as an asset (no upload needed)."""
    from amplify.db.models.asset import AssetModel

    asset = AssetModel(
        tenant_id=tenant_id,
        artist_id=body.artist_id,
        release_id=body.release_id,
        campaign_id=body.campaign_id,
        asset_type=body.asset_type,
        name=body.name,
        description=body.description,
        file_url=file_url,
        tags=body.tags,
        source=body.source,
    )
    db.add(asset)
    await db.flush()
    await db.refresh(asset)
    return asset


@router.get("/{asset_id}", response_model=AssetResponse)
async def get_asset(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Get a single asset by ID."""
    from amplify.db.models.asset import AssetModel

    result = await db.execute(
        select(AssetModel).where(
            AssetModel.id == asset_id,
            AssetModel.tenant_id == tenant_id,
        )
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")
    return asset


@router.put("/{asset_id}", response_model=AssetResponse)
async def update_asset(
    asset_id: uuid.UUID,
    body: AssetUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Update asset metadata (name, tags, description, associations)."""
    from amplify.db.models.asset import AssetModel

    result = await db.execute(
        select(AssetModel).where(
            AssetModel.id == asset_id,
            AssetModel.tenant_id == tenant_id,
        )
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(asset, field, value)

    await db.flush()
    await db.refresh(asset)
    return asset


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Delete an asset."""
    from amplify.db.models.asset import AssetModel

    result = await db.execute(
        select(AssetModel).where(
            AssetModel.id == asset_id,
            AssetModel.tenant_id == tenant_id,
        )
    )
    asset = result.scalar_one_or_none()
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    await db.delete(asset)
    return None

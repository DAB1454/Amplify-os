"""Asset library routes — upload, browse, tag, and manage media assets."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.deps import get_db, get_settings, get_tenant_id
from pydantic import BaseModel, Field as PydanticField

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
    approval: str | None = Query(None, description="Filter by approval_status: pending_review, approved, rejected"),
    include_rejected: bool = Query(False, description="Include rejected assets"),
):
    """List assets in the tenant's library with optional filters."""
    from amplify.db.models.asset import AssetModel
    from sqlalchemy import or_

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
    if approval:
        q = q.where(AssetModel.approval_status == approval)
    elif not include_rejected:
        # By default, exclude rejected assets
        q = q.where(or_(AssetModel.approval_status != "rejected", AssetModel.approval_status.is_(None)))

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


@router.post("/{asset_id}/approve", response_model=AssetResponse)
async def approve_asset(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Approve an AI-generated asset to add it to the active library."""
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

    asset.approval_status = "approved"
    # Remove pending_review tag
    if asset.tags and "pending_review" in asset.tags:
        asset.tags = [t for t in asset.tags if t != "pending_review"]
    await db.flush()
    await db.refresh(asset)
    return asset


@router.post("/{asset_id}/reject", response_model=AssetResponse)
async def reject_asset(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Reject an AI-generated asset."""
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

    asset.approval_status = "rejected"
    await db.flush()
    await db.refresh(asset)
    return asset


# ── Bulk Import ──────────────────────────────────────────────────


class BulkImportRequest(BaseModel):
    """Import assets from external URLs or an S3 prefix."""
    urls: list[str] = PydanticField(default_factory=list, description="Direct URLs to import")
    s3_prefix: str = PydanticField(default="", description="S3 prefix to scan (e.g. s3://bucket/artist-photos/)")
    asset_type: str = "image"
    artist_id: uuid.UUID | None = None
    release_id: uuid.UUID | None = None
    tags: list[str] = PydanticField(default_factory=list)


@router.post("/import", response_model=list[AssetResponse])
async def bulk_import_assets(
    body: BulkImportRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    svc: MediaService = Depends(_get_media_service),
):
    """Bulk import assets from URLs or S3 prefix.

    - **urls**: List of direct file URLs (S3, CDN, any public URL)
    - **s3_prefix**: Scan an S3 bucket prefix and import all files found

    Files are not re-uploaded — we store the original URL as a reference.
    For S3 prefixes, the service lists objects and creates asset records.
    """
    from amplify.db.models.asset import AssetModel
    import mimetypes

    created = []

    # Import from direct URLs
    for url in body.urls:
        filename = url.rstrip("/").split("/")[-1].split("?")[0] or "imported-file"
        mime, _ = mimetypes.guess_type(filename)

        asset = AssetModel(
            tenant_id=tenant_id,
            artist_id=body.artist_id,
            release_id=body.release_id,
            asset_type=body.asset_type,
            name=filename,
            file_url=url,
            mime_type=mime,
            tags=body.tags,
            source="imported",
        )
        db.add(asset)
        created.append(asset)

    # Import from S3 prefix
    if body.s3_prefix:
        try:
            s3_assets = await _scan_s3_prefix(
                svc=svc,
                prefix=body.s3_prefix,
                tenant_id=tenant_id,
                artist_id=body.artist_id,
                release_id=body.release_id,
                asset_type=body.asset_type,
                tags=body.tags,
            )
            for asset in s3_assets:
                db.add(asset)
                created.append(asset)
        except Exception as exc:
            logger.error("S3 prefix scan failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=400, detail=f"S3 scan failed: {exc}")

    if created:
        await db.flush()
        for asset in created:
            await db.refresh(asset)

    return created


async def _scan_s3_prefix(
    *,
    svc: MediaService,
    prefix: str,
    tenant_id: uuid.UUID,
    artist_id: uuid.UUID | None,
    release_id: uuid.UUID | None,
    asset_type: str,
    tags: list[str],
) -> list:
    """List objects under an S3 prefix and create asset records."""
    import asyncio
    import mimetypes
    from amplify.db.models.asset import AssetModel

    # Parse s3://bucket/prefix or just a prefix
    bucket = svc.s3_bucket
    key_prefix = prefix

    if prefix.startswith("s3://"):
        parts = prefix[5:].split("/", 1)
        bucket = parts[0]
        key_prefix = parts[1] if len(parts) > 1 else ""
    elif prefix.startswith("https://") and ".s3." in prefix:
        # https://bucket.s3.region.amazonaws.com/prefix/
        from urllib.parse import urlparse
        parsed = urlparse(prefix)
        bucket = parsed.hostname.split(".")[0] if parsed.hostname else svc.s3_bucket
        key_prefix = parsed.path.lstrip("/")

    if not bucket:
        raise ValueError("No S3 bucket configured. Set s3_prefix as s3://bucket/path/ or configure S3_BUCKET.")

    client = svc._get_s3_client()

    def _list():
        paginator = client.get_paginator("list_objects_v2")
        objects = []
        for page in paginator.paginate(Bucket=bucket, Prefix=key_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                size = obj.get("Size", 0)
                # Skip directories and tiny files
                if key.endswith("/") or size < 100:
                    continue
                objects.append({"key": key, "size": size})
        return objects

    objects = await asyncio.get_event_loop().run_in_executor(None, _list)

    assets = []
    region = svc.s3_region or "us-east-1"
    for obj in objects:
        key = obj["key"]
        filename = key.split("/")[-1]
        mime, _ = mimetypes.guess_type(filename)

        # Build public URL
        if svc._media_base_url:
            url = f"{svc._media_base_url.rstrip('/')}/{key}"
        else:
            url = f"https://{bucket}.s3.{region}.amazonaws.com/{key}"

        asset = AssetModel(
            tenant_id=tenant_id,
            artist_id=artist_id,
            release_id=release_id,
            asset_type=asset_type,
            name=filename,
            file_url=url,
            file_size_bytes=obj["size"],
            mime_type=mime,
            tags=tags,
            source="imported",
        )
        assets.append(asset)

    return assets

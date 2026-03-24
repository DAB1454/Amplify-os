"""Media upload routes — video, audio, and image file handling."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import Settings
from app.deps import get_settings, get_tenant_id
from app.services.media_service import (
    ALLOWED_TYPES,
    MAX_FILE_SIZE,
    UPLOAD_DIR,
    MediaService,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/media", tags=["media"])


def _get_media_service(settings: Settings = Depends(get_settings)) -> MediaService:
    return MediaService(
        s3_bucket=settings.s3_bucket,
        s3_region=settings.s3_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        media_base_url=settings.media_base_url,
    )


@router.post("/upload")
async def upload_media(
    file: UploadFile = File(..., description="Video, audio, or image file"),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    svc: MediaService = Depends(_get_media_service),
):
    """Upload a media file. Returns the URL to use in post media_urls.

    Streams directly to S3 without buffering the entire file in memory.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{content_type}' not allowed. Supported: video, audio, images.",
        )

    # Stream to a temp file to avoid holding everything in memory.
    # FastAPI's UploadFile already uses a SpooledTemporaryFile, but it
    # spools to memory for small files. For large uploads we need disk.
    import tempfile
    tmp = tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024)  # spool >2MB to disk
    total_size = 0
    chunk_size = 256 * 1024  # 256 KB chunks

    try:
        while True:
            chunk = await file.read(chunk_size)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > MAX_FILE_SIZE:
                tmp.close()
                raise HTTPException(
                    status_code=400,
                    detail=f"File too large (>{MAX_FILE_SIZE // (1024*1024)} MB).",
                )
            tmp.write(chunk)

        tmp.seek(0)

        try:
            url = await svc.upload(tenant_id, tmp, file.filename, content_type)
        except Exception as exc:
            logger.error("Media upload failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=f"Upload failed: {exc}")
    finally:
        tmp.close()

    return {
        "url": url,
        "filename": file.filename,
        "content_type": content_type,
        "size": total_size,
    }


@router.post("/merge")
async def merge_media(
    video_url: str,
    audio_url: str,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    svc: MediaService = Depends(_get_media_service),
):
    """Merge a video file with an audio track. Returns the URL of the merged file.

    Both video_url and audio_url should be URLs (e.g. from prior /upload calls).
    The video's original audio is replaced with the provided audio track.
    """
    from app.services.merge_service import merge_video_audio

    if not video_url or not audio_url:
        raise HTTPException(status_code=400, detail="Both video_url and audio_url are required")

    try:
        merged_path = await merge_video_audio(video_url, audio_url)
    except Exception as exc:
        logger.error("Merge failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Merge failed: {exc}")

    try:
        import io
        with open(merged_path, "rb") as f:
            merged_bytes = f.read()
        file_obj = io.BytesIO(merged_bytes)
        url = await svc.upload(tenant_id, file_obj, f"merged-{uuid.uuid4()}.mp4", "video/mp4")
    except Exception as exc:
        logger.error("Upload of merged file failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}")
    finally:
        merged_path.unlink(missing_ok=True)

    return {
        "url": url,
        "size": len(merged_bytes),
    }


@router.get("/files/{path:path}")
async def serve_media(path: str):
    """Serve locally-stored media files (dev/fallback when S3 not configured)."""
    file_path = UPLOAD_DIR / path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    # Security: ensure path doesn't escape upload dir
    if not str(file_path.resolve()).startswith(str(UPLOAD_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    return FileResponse(file_path)

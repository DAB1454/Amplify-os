"""Media upload service — S3 when configured, local disk fallback."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import BinaryIO

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path("/tmp/amplify-media")
MAX_FILE_SIZE = 500 * 1024 * 1024  # 500 MB
ALLOWED_TYPES = {
    # Video
    "video/mp4", "video/quicktime", "video/x-msvideo", "video/webm",
    "video/x-matroska", "video/mpeg",
    # Audio
    "audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/ogg",
    "audio/flac", "audio/aac", "audio/x-m4a", "audio/mp4",
    # Images
    "image/jpeg", "image/png", "image/webp", "image/gif",
}


class MediaService:
    """Handles media file uploads to S3 or local disk."""

    def __init__(
        self,
        s3_bucket: str = "",
        s3_region: str = "us-east-1",
        aws_access_key_id: str = "",
        aws_secret_access_key: str = "",
        media_base_url: str = "",
    ) -> None:
        self.s3_bucket = s3_bucket
        self.s3_region = s3_region
        self._aws_key = aws_access_key_id
        self._aws_secret = aws_secret_access_key
        self._media_base_url = media_base_url
        self._s3_client = None

    @property
    def use_s3(self) -> bool:
        return bool(self.s3_bucket and self._aws_key and self._aws_secret)

    def _get_s3_client(self):
        if self._s3_client is None:
            import boto3
            self._s3_client = boto3.client(
                "s3",
                region_name=self.s3_region,
                aws_access_key_id=self._aws_key,
                aws_secret_access_key=self._aws_secret,
            )
        return self._s3_client

    async def upload(
        self,
        tenant_id: uuid.UUID,
        file_data: BinaryIO,
        filename: str,
        content_type: str,
    ) -> str:
        """Upload a file and return its public URL.

        Uses S3 if configured, otherwise saves to local disk.
        """
        ext = Path(filename).suffix.lower() or ".bin"
        file_id = uuid.uuid4()
        key = f"media/{tenant_id}/{file_id}{ext}"

        if self.use_s3:
            return await self._upload_s3(key, file_data, content_type)
        else:
            return self._upload_local(key, file_data)

    async def _upload_s3(
        self, key: str, file_data: BinaryIO, content_type: str
    ) -> str:
        """Upload to S3 and return the public URL."""
        import asyncio

        client = self._get_s3_client()

        def _do_upload():
            client.upload_fileobj(
                file_data,
                self.s3_bucket,
                key,
                ExtraArgs={
                    "ContentType": content_type,
                    "CacheControl": "public, max-age=31536000",
                },
            )

        await asyncio.get_event_loop().run_in_executor(None, _do_upload)

        if self._media_base_url:
            return f"{self._media_base_url.rstrip('/')}/{key}"
        return f"https://{self.s3_bucket}.s3.{self.s3_region}.amazonaws.com/{key}"

    def _upload_local(self, key: str, file_data: BinaryIO) -> str:
        """Save to local disk and return a relative API URL."""
        dest = UPLOAD_DIR / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            while chunk := file_data.read(1024 * 1024):
                f.write(chunk)
        logger.info("Saved media locally: %s", dest)
        # Return API-relative URL that the serve endpoint will handle
        return f"/api/v1/media/files/{key}"

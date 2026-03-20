"""Asset domain models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .post import Platform


class AssetType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"


class Asset(BaseModel):
    """A media asset (image, video, audio, document)."""

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    release_id: UUID | None = None
    campaign_id: UUID | None = None
    asset_type: AssetType
    name: str
    file_url: str
    file_size_bytes: int | None = None
    mime_type: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AssetVariant(BaseModel):
    """A platform-specific variant of an asset (e.g., resized for Instagram)."""

    id: UUID = Field(default_factory=uuid4)
    asset_id: UUID
    platform: Platform
    file_url: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    format: str | None = None


class AssetCreate(BaseModel):
    """Schema for creating a new asset."""

    release_id: UUID | None = None
    campaign_id: UUID | None = None
    asset_type: AssetType
    name: str
    file_url: str
    file_size_bytes: int | None = None
    mime_type: str | None = None
    metadata: dict = Field(default_factory=dict)


class AssetVariantCreate(BaseModel):
    """Schema for creating a new asset variant."""

    asset_id: UUID
    platform: Platform
    file_url: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    format: str | None = None

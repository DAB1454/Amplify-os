"""Release domain models."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ReleaseType(str, Enum):
    SINGLE = "single"
    EP = "ep"
    ALBUM = "album"
    COMPILATION = "compilation"


class ReleaseStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RELEASED = "released"
    ARCHIVED = "archived"


class Release(BaseModel):
    """A music release (single, EP, album, etc.)."""

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    artist_id: UUID
    title: str
    release_type: ReleaseType
    status: ReleaseStatus = ReleaseStatus.DRAFT
    release_date: date | None = None
    upc: str | None = None
    artwork_url: str | None = None
    metadata: dict = Field(default_factory=dict)
    hyperfollow_url: str | None = None
    linktree_url: str | None = None
    bandcamp_url: str | None = None
    youtube_url: str | None = None
    tiktok_url: str | None = None
    instagram_url: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ReleaseCreate(BaseModel):
    """Schema for creating a new release."""

    artist_id: UUID
    title: str
    release_type: ReleaseType
    release_date: date | None = None
    upc: str | None = None
    artwork_url: str | None = None
    metadata: dict = Field(default_factory=dict)
    hyperfollow_url: str | None = None
    linktree_url: str | None = None
    bandcamp_url: str | None = None
    youtube_url: str | None = None
    tiktok_url: str | None = None
    instagram_url: str | None = None


class ReleaseUpdate(BaseModel):
    """Schema for updating an existing release."""

    title: str | None = None
    release_type: ReleaseType | None = None
    status: ReleaseStatus | None = None
    release_date: date | None = None
    upc: str | None = None
    artwork_url: str | None = None
    metadata: dict | None = None
    hyperfollow_url: str | None = None
    linktree_url: str | None = None
    bandcamp_url: str | None = None
    youtube_url: str | None = None
    tiktok_url: str | None = None
    instagram_url: str | None = None

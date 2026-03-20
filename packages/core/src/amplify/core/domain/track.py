"""Track domain models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Track(BaseModel):
    """A track belonging to a release."""

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    release_id: UUID
    title: str
    track_number: int
    duration_seconds: int | None = None
    isrc: str | None = None
    audio_url: str | None = None
    lyrics: str | None = None
    is_single: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class TrackCreate(BaseModel):
    """Schema for creating a new track."""

    release_id: UUID
    title: str
    track_number: int
    duration_seconds: int | None = None
    isrc: str | None = None
    audio_url: str | None = None
    lyrics: str | None = None
    is_single: bool = False


class TrackUpdate(BaseModel):
    """Schema for updating an existing track."""

    title: str | None = None
    track_number: int | None = None
    duration_seconds: int | None = None
    isrc: str | None = None
    audio_url: str | None = None
    lyrics: str | None = None
    is_single: bool | None = None

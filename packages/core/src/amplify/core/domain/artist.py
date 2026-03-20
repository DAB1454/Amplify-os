"""Artist domain models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Artist(BaseModel):
    """An artist managed within a tenant."""

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    name: str
    slug: str
    bio: str = ""
    genre: str = ""
    image_url: str | None = None
    spotify_id: str | None = None
    social_links: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ArtistCreate(BaseModel):
    """Schema for creating a new artist."""

    name: str
    bio: str = ""
    genre: str = ""
    image_url: str | None = None
    spotify_id: str | None = None
    social_links: dict = Field(default_factory=dict)


class ArtistUpdate(BaseModel):
    """Schema for updating an existing artist."""

    name: str | None = None
    bio: str | None = None
    genre: str | None = None
    image_url: str | None = None
    spotify_id: str | None = None
    social_links: dict | None = None

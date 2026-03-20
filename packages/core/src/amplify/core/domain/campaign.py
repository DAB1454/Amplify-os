"""Campaign domain models."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class CampaignStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class CampaignPhase(str, Enum):
    PRE_RELEASE = "pre_release"
    RELEASE_DAY = "release_day"
    POST_RELEASE = "post_release"
    EVERGREEN = "evergreen"


class Campaign(BaseModel):
    """A marketing campaign tied to an artist and optionally a release."""

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    artist_id: UUID
    release_id: UUID | None = None
    name: str
    status: CampaignStatus = CampaignStatus.DRAFT
    phase: CampaignPhase = CampaignPhase.PRE_RELEASE
    start_date: date | None = None
    end_date: date | None = None
    budget: float | None = None
    goals: dict = Field(default_factory=dict)
    config: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CampaignCreate(BaseModel):
    """Schema for creating a new campaign."""

    artist_id: UUID
    release_id: UUID | None = None
    name: str
    phase: CampaignPhase = CampaignPhase.PRE_RELEASE
    start_date: date | None = None
    end_date: date | None = None
    budget: float | None = None
    goals: dict = Field(default_factory=dict)
    config: dict = Field(default_factory=dict)


class CampaignUpdate(BaseModel):
    """Schema for updating an existing campaign."""

    name: str | None = None
    status: CampaignStatus | None = None
    phase: CampaignPhase | None = None
    start_date: date | None = None
    end_date: date | None = None
    budget: float | None = None
    goals: dict | None = None
    config: dict | None = None

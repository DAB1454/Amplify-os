"""Metric domain models."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MetricSource(str, Enum):
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"
    BANDCAMP = "bandcamp"
    EMAIL = "email"
    WEBSITE = "website"
    INTERNAL = "internal"


class MetricEvent(BaseModel):
    """A single metric event (e.g., a stream, a click, a follow)."""

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    source: MetricSource
    event_type: str
    entity_type: str
    entity_id: UUID
    value: float
    metadata: dict = Field(default_factory=dict)
    occurred_at: datetime = Field(default_factory=datetime.utcnow)


class DailyMetric(BaseModel):
    """Aggregated daily metric for an entity."""

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    source: MetricSource
    metric_name: str
    entity_type: str
    entity_id: UUID
    date: date
    value: float
    previous_value: float | None = None


class MetricEventCreate(BaseModel):
    """Schema for creating a metric event."""

    source: MetricSource
    event_type: str
    entity_type: str
    entity_id: UUID
    value: float
    metadata: dict = Field(default_factory=dict)
    occurred_at: datetime | None = None


class DailyMetricCreate(BaseModel):
    """Schema for creating a daily metric."""

    source: MetricSource
    metric_name: str
    entity_type: str
    entity_id: UUID
    date: date
    value: float
    previous_value: float | None = None

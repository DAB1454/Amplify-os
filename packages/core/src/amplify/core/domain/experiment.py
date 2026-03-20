"""Experiment domain models with hypothesis, variables, cohorts, and outcomes."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ExperimentStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class OutcomeVerdict(str, Enum):
    """Analyst recommendation after experiment concludes."""
    KEEP = "keep"
    REMIX = "remix"
    STOP = "stop"
    INCONCLUSIVE = "inconclusive"


class ExperimentVariable(BaseModel):
    """A single independent variable being tested."""
    name: str
    description: str = ""
    values: list[str] = Field(default_factory=list)


class ExperimentVariant(BaseModel):
    """A variant (arm) of the experiment with its variable values."""
    id: int = 0
    label: str = ""
    variable_values: dict[str, str] = Field(default_factory=dict)
    post_ids: list[UUID] = Field(default_factory=list)


class ExperimentOutcome(BaseModel):
    """Results summary for an experiment."""
    winner_variant_id: int | None = None
    verdict: OutcomeVerdict = OutcomeVerdict.INCONCLUSIVE
    summary: str = ""
    metrics_summary: dict[str, float] = Field(default_factory=dict)
    confidence: float = 0.0
    evaluated_at: datetime | None = None


class Experiment(BaseModel):
    """An A/B or multivariate experiment within a campaign."""

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    campaign_id: UUID
    name: str
    hypothesis: str = ""
    status: ExperimentStatus = ExperimentStatus.DRAFT

    # Independent variables being tested
    variables: list[ExperimentVariable] = Field(default_factory=list)

    # Experiment arms
    variants: list[ExperimentVariant] = Field(default_factory=list)

    # Cohort
    cohort_platform: str = ""
    cohort_size: int = 0

    # Outcome
    outcome: ExperimentOutcome | None = None
    winner_variant: int | None = None

    # Config
    metrics_config: dict = Field(default_factory=dict)
    start_date: date | None = None
    end_date: date | None = None

    # Timestamps
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ExperimentCreate(BaseModel):
    """Schema for creating a new experiment."""

    campaign_id: UUID
    name: str
    hypothesis: str = ""
    variables: list[dict] = Field(default_factory=list)
    variants: list[dict] = Field(default_factory=list)
    cohort_platform: str = ""
    cohort_size: int = 0
    start_date: date | None = None
    end_date: date | None = None
    metrics_config: dict = Field(default_factory=dict)


class ExperimentUpdate(BaseModel):
    """Schema for updating an existing experiment."""

    name: str | None = None
    hypothesis: str | None = None
    status: ExperimentStatus | None = None
    variables: list[dict] | None = None
    variants: list[dict] | None = None
    winner_variant: int | None = None
    metrics_config: dict | None = None
    cohort_platform: str | None = None
    cohort_size: int | None = None
    start_date: date | None = None
    end_date: date | None = None

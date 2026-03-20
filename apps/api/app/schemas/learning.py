"""Pydantic request/response schemas for the learning subsystem."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# ── LearningEvent ────────────────────────────────────────────────


class LearningEventCreate(BaseModel):
    action_type: str = Field(max_length=50)
    idempotency_key: str | None = None
    action_id: uuid.UUID | None = None
    artist_id: uuid.UUID | None = None
    release_id: uuid.UUID | None = None
    campaign_id: uuid.UUID | None = None
    post_id: uuid.UUID | None = None
    asset_id: uuid.UUID | None = None
    platform: str | None = None
    features: dict = Field(default_factory=dict)
    outcomes: dict = Field(default_factory=dict)
    reward: float | None = None
    reward_breakdown: dict | None = None
    observed_at: datetime
    outcome_measured_at: datetime | None = None
    source: str = ""
    confidence: float = 1.0
    schema_version: int = 1


class LearningEventResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    action_type: str
    idempotency_key: str | None
    action_id: uuid.UUID | None
    artist_id: uuid.UUID | None
    release_id: uuid.UUID | None
    campaign_id: uuid.UUID | None
    post_id: uuid.UUID | None
    asset_id: uuid.UUID | None
    platform: str | None
    features: dict
    outcomes: dict
    reward: float | None
    reward_breakdown: dict | None
    observed_at: datetime
    outcome_measured_at: datetime | None
    source: str
    confidence: float
    schema_version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class IngestRequest(BaseModel):
    """Batch ingest request for learning events."""
    events: list[LearningEventCreate] = Field(min_length=1, max_length=500)


class IngestResponse(BaseModel):
    """Result of a batch ingest operation."""
    created: int
    skipped: int
    errors: list[dict]
    total: int


# ── PostFeatureVector ────────────────────────────────────────────


class PostFeatureVectorCreate(BaseModel):
    post_id: uuid.UUID
    artist_id: uuid.UUID | None = None
    campaign_id: uuid.UUID | None = None
    features: dict
    platform: str | None = None
    content_type: str | None = None
    post_hour: int | None = None
    post_day_of_week: int | None = None
    caption_length: int | None = None
    hashtag_count: int | None = None
    has_link: bool | None = None
    campaign_phase: str | None = None
    schema_version: int = 1


class PostFeatureVectorResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    post_id: uuid.UUID
    artist_id: uuid.UUID | None
    campaign_id: uuid.UUID | None
    features: dict
    platform: str | None
    content_type: str | None
    post_hour: int | None
    post_day_of_week: int | None
    caption_length: int | None
    hashtag_count: int | None
    has_link: bool | None
    campaign_phase: str | None
    extracted_at: datetime
    schema_version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── PostOutcome ──────────────────────────────────────────────────


class PostOutcomeCreate(BaseModel):
    post_id: uuid.UUID
    measurement_window: str = Field(max_length=20)
    impressions: int | None = None
    reach: int | None = None
    engagements: int | None = None
    saves: int | None = None
    shares: int | None = None
    clicks: int | None = None
    comments_count: int | None = None
    followers_gained: int | None = None
    engagement_rate: float | None = None
    save_rate: float | None = None
    click_through_rate: float | None = None
    reward: float | None = None
    reward_breakdown: dict | None = None
    measured_at: datetime
    source: str = ""


class PostOutcomeResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    post_id: uuid.UUID
    measurement_window: str
    impressions: int | None
    reach: int | None
    engagements: int | None
    saves: int | None
    shares: int | None
    clicks: int | None
    comments_count: int | None
    followers_gained: int | None
    engagement_rate: float | None
    save_rate: float | None
    click_through_rate: float | None
    reward: float | None
    reward_breakdown: dict | None
    measured_at: datetime
    source: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── TenantPattern ────────────────────────────────────────────────


class TenantPatternResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    artist_id: uuid.UUID | None
    pattern_type: str
    subject_feature: str | None
    subject_value: str | None
    title: str
    explanation: str
    confidence: float
    supporting_observation_count: int
    observation_window_start: datetime | None
    observation_window_end: datetime | None
    decay_weighted_reward: float | None
    sample_count_decayed: float | None
    evidence: dict | None
    is_pinned: bool
    pinned_by: uuid.UUID | None
    pinned_at: datetime | None
    direction: str
    is_active: bool
    superseded_by_id: uuid.UUID | None
    source: str
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RecommendationResponse(BaseModel):
    feature: str
    recommended_value: str
    title: str
    explanation: str
    confidence: float
    evidence: dict
    pattern_type: str


class BlendedRecommendationResponse(BaseModel):
    feature: str
    recommended_value: str
    title: str
    explanation: str
    confidence: float
    source: str  # "global_prior", "cohort_prior", "tenant_learned", "blended"
    category: str = ""
    evidence: dict = {}
    pattern_type: str = ""
    tenant_weight: float = 0.0
    global_weight: float = 1.0
    tenant_confidence: float | None = None
    global_confidence: float | None = None
    global_tenant_count: int | None = None


class PatternDataStatus(BaseModel):
    feature_vectors: int
    outcomes: int
    active_patterns: int
    min_required: int
    has_enough_data: bool


class DataMaturityStatus(BaseModel):
    feature_vectors: int
    outcomes: int
    active_patterns: int
    min_required: int
    has_enough_data: bool
    is_cold_start: bool = True
    maturity_label: str = "no_data"  # "no_data", "early", "growing", "mature"
    tenant_weight: float = 0.0
    global_weight: float = 1.0


class PatternsListResponse(BaseModel):
    patterns: list[TenantPatternResponse]
    data_status: PatternDataStatus


class RecommendationsListResponse(BaseModel):
    recommendations: list[RecommendationResponse]
    data_status: PatternDataStatus


class BlendedRecommendationsResponse(BaseModel):
    recommendations: list[BlendedRecommendationResponse]
    data_status: DataMaturityStatus


# ── GlobalPatternStat ────────────────────────────────────────────


class GlobalPatternStatResponse(BaseModel):
    id: uuid.UUID
    feature_name: str
    feature_value: str
    action_type: str
    mean_reward: float
    std_dev: float | None
    confidence_lower: float | None
    confidence_upper: float | None
    observation_count: int
    tenant_count: int
    k_anonymity_threshold: int
    computed_at: datetime
    version: int
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── CohortDefinition ────────────────────────────────────────────


class CohortDefinitionCreate(BaseModel):
    name: str = Field(max_length=255)
    description: str = ""
    criteria: dict


class CohortDefinitionResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str
    criteria: dict
    member_count: int
    last_computed_at: datetime | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── PromptVersion ────────────────────────────────────────────────


class PromptVersionCreate(BaseModel):
    prompt_key: str = Field(max_length=100)
    version: int
    template: str
    system_prompt: str | None = None
    model_id: str | None = None
    parameters: dict = Field(default_factory=dict)
    description: str = ""
    author: str | None = None


class PromptVersionResponse(BaseModel):
    id: uuid.UUID
    prompt_key: str
    version: int
    template: str
    system_prompt: str | None
    model_id: str | None
    parameters: dict
    description: str
    author: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── PromptAssignment ─────────────────────────────────────────────


class PromptAssignmentCreate(BaseModel):
    prompt_key: str = Field(max_length=100)
    prompt_version_id: uuid.UUID
    reason: str = ""
    assigned_by: uuid.UUID | None = None


class PromptAssignmentResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    prompt_key: str
    prompt_version_id: uuid.UUID
    reason: str
    assigned_by: uuid.UUID | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── EvaluationRun ────────────────────────────────────────────────


class EvaluationRunResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    evaluation_type: str
    scope: str
    window_start: datetime
    window_end: datetime
    sample_size: int
    mae: float | None
    rank_correlation: float | None
    top_k_precision: float | None
    top_k: int
    details: dict
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EvaluationResultResponse(BaseModel):
    id: uuid.UUID
    evaluation_run_id: uuid.UUID
    action_id: uuid.UUID | None
    action_type: str
    predicted_score: float
    predicted_rank: int | None
    actual_reward: float | None
    actual_rank: int | None
    absolute_error: float | None
    explanation: dict | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── LearningAuditLog ────────────────────────────────────────────


# ── Backfill ────────────────────────────────────────────────────


class BackfillRequest(BaseModel):
    """Request body for triggering a backfill."""
    batch_size: int = Field(default=200, ge=1, le=5000)
    skip_features: bool = False
    skip_outcomes: bool = False
    skip_events: bool = False
    bootstrap_patterns: bool = True
    cursor: str | None = None
    dry_run: bool = False


class BackfillReportResponse(BaseModel):
    """Backfill run report."""
    tenant_id: str
    started_at: str
    completed_at: str
    status: str

    posts_scanned: int
    features_created: int
    features_skipped: int
    features_errors: int
    outcomes_created: int
    outcomes_skipped: int
    outcomes_errors: int
    events_created: int
    events_skipped: int
    events_errors: int
    patterns_created: int

    cursor: str | None
    is_complete: bool
    warnings: list[str]
    errors: list[dict]


class LearningAuditLogResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    user_id: uuid.UUID | None
    details: dict
    explanation: str | None
    schema_version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

"""Learning subsystem ORM models.

Provides persistent storage for the learning pipeline:
- Events and observations (LearningEvent, PostFeatureVector, PostOutcome)
- Pattern storage (TenantPattern, GlobalPatternStat)
- Cohort management (CohortDefinition)
- Prompt versioning (PromptVersion, PromptAssignment)
- Evaluation (EvaluationRun, EvaluationResult)
- Audit trail (LearningAuditLog)
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from amplify.db.base import Base, TimestampMixin, TenantMixin


# ── LearningEvent ───────────────────────────────────────────────────


class LearningEventModel(Base, TimestampMixin, TenantMixin):
    """Raw learning event — the atomic unit of data entering the learning pipeline.

    Records any action-outcome pair: a post published, a campaign launched,
    a scheduling decision made.  Features and outcomes are stored as JSON
    for schema flexibility during early iterations.
    """

    __tablename__ = "learning_events"
    __table_args__ = (
        Index(
            "ix_learning_events_tenant_action_observed",
            "tenant_id", "action_type", "observed_at",
        ),
        Index(
            "ix_learning_events_tenant_artist",
            "tenant_id", "artist_id",
        ),
        UniqueConstraint("idempotency_key", name="uq_learning_events_idempotency_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )

    # What action was taken
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    action_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    # FK references (all nullable for flexibility)
    artist_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artists.id"), nullable=True
    )
    release_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("releases.id"), nullable=True
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaigns.id"), nullable=True
    )
    post_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("posts.id"), nullable=True
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assets.id"), nullable=True
    )

    # Context
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)
    features: Mapped[dict] = mapped_column(JSON, default=dict)

    # Outcomes
    outcomes: Mapped[dict] = mapped_column(JSON, default=dict)
    reward: Mapped[float | None] = mapped_column(Float, nullable=True)
    reward_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Timing
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    outcome_measured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Provenance
    source: Mapped[str] = mapped_column(String(100), default="")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)

    # Idempotency — prevents duplicate events on replay/retry
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )

    # Versioning — which feature schema version produced this event
    schema_version: Mapped[int] = mapped_column(Integer, default=1)


# ── PostFeatureVector ───────────────────────────────────────────────


class PostFeatureVectorModel(Base, TimestampMixin, TenantMixin):
    """Extracted feature vector for a specific post.

    One row per post.  Stores the feature vector computed at publish time
    so that it can be replayed for evaluation without re-extracting.
    """

    __tablename__ = "post_feature_vectors"
    __table_args__ = (
        UniqueConstraint("post_id", name="uq_post_feature_vectors_post_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    post_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("posts.id"), nullable=False
    )
    artist_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artists.id"), nullable=True
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaigns.id"), nullable=True
    )

    # The feature vector (JSON dict of feature_name → value)
    features: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Individual typed features for indexed queries
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    post_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    post_day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    caption_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hashtag_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_link: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    campaign_phase: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Extraction provenance
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    schema_version: Mapped[int] = mapped_column(Integer, default=1)


# ── PostOutcome ─────────────────────────────────────────────────────


class PostOutcomeModel(Base, TimestampMixin, TenantMixin):
    """Measured outcomes for a post, collected after publication.

    May be updated multiple times as metrics trickle in (24h, 48h, 7d).
    The `measurement_window` field tracks which collection pass this is.
    """

    __tablename__ = "post_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "post_id", "measurement_window",
            name="uq_post_outcomes_post_window",
        ),
        Index("ix_post_outcomes_tenant_post", "tenant_id", "post_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    post_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("posts.id"), nullable=False
    )

    # Which measurement pass (e.g. "24h", "48h", "7d", "final")
    measurement_window: Mapped[str] = mapped_column(String(20), nullable=False)

    # Raw metrics (all nullable for partial backfill)
    impressions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reach: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engagements: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shares: Mapped[int | None] = mapped_column(Integer, nullable=True)
    clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    followers_gained: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Computed rates
    engagement_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    save_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    click_through_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Reward score (computed by RewardCalculator)
    reward: Mapped[float | None] = mapped_column(Float, nullable=True)
    reward_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # When the metrics were collected
    measured_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    source: Mapped[str] = mapped_column(String(100), default="")


# ── TenantPattern ───────────────────────────────────────────────────


class TenantPatternModel(Base, TimestampMixin, TenantMixin):
    """A learned pattern for a specific tenant — the persistent form of an Insight.

    Examples: "posts at 18:00 get 2x engagement", "carousel posts outperform
    single images on Instagram for this artist".
    """

    __tablename__ = "tenant_patterns"
    __table_args__ = (
        Index(
            "ix_tenant_patterns_tenant_type_feature",
            "tenant_id", "pattern_type", "subject_feature",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    artist_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artists.id"), nullable=True
    )

    # Pattern identity
    pattern_type: Mapped[str] = mapped_column(String(30), nullable=False)  # timing, content, platform, audience, campaign, anomaly
    subject_feature: Mapped[str | None] = mapped_column(String(100), nullable=True)
    subject_value: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Human-readable
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)

    # Statistical evidence
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    supporting_observation_count: Mapped[int] = mapped_column(Integer, default=0)
    observation_window_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    observation_window_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Decay-weighted statistics
    decay_weighted_reward: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_count_decayed: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Pinning — admin can lock a pattern so nightly jobs don't overwrite it
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    pinned_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Direction — is this pattern "winning" or "losing"?
    direction: Mapped[str] = mapped_column(String(10), default="winning")  # "winning" or "losing"

    # State
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenant_patterns.id"), nullable=True
    )
    source: Mapped[str] = mapped_column(String(30), default="tenant")  # "tenant" or "global_prior"

    # Versioning
    version: Mapped[int] = mapped_column(Integer, default=1)


# ── GlobalPatternStat ───────────────────────────────────────────────


class GlobalPatternStatModel(Base, TimestampMixin):
    """Cross-tenant aggregate statistic — the persistent form of a GlobalPrior.

    Contains only aggregate statistics, never individual tenant data.
    Must meet k-anonymity thresholds before being stored.
    """

    __tablename__ = "global_pattern_stats"
    __table_args__ = (
        UniqueConstraint(
            "feature_name", "feature_value", "action_type",
            name="uq_global_pattern_stats_feature_action",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # What this stat is about
    feature_name: Mapped[str] = mapped_column(String(100), nullable=False)
    feature_value: Mapped[str] = mapped_column(String(255), nullable=False)
    action_type: Mapped[str] = mapped_column(String(50), default="")

    # Aggregate statistics
    mean_reward: Mapped[float] = mapped_column(Float, nullable=False)
    std_dev: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    tenant_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # Source cohort (NULL = global aggregate, non-NULL = cohort-scoped)
    source_cohort_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cohort_definitions.id"), nullable=True
    )

    # Pattern category — what domain this prior covers
    # e.g. "posting_window", "cta_type", "format_preference", "hook_family"
    category: Mapped[str] = mapped_column(String(50), default="")

    # Privacy metadata
    k_anonymity_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Versioning
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ── CohortDefinition ───────────────────────────────────────────────


class CohortDefinitionModel(Base, TimestampMixin, TenantMixin):
    """Defines a segment of tenants or artists for targeted learning.

    Used to scope evaluations, priors, and experiments to meaningful
    sub-populations (e.g. "hip-hop artists", "tenants with >100 posts").
    """

    __tablename__ = "cohort_definitions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")

    # Filter criteria (JSON DSL for flexible querying)
    # e.g. {"genre": "hip-hop", "min_posts": 50, "platforms": ["instagram"]}
    criteria: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Membership (computed, cached)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    last_computed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ── PromptVersion ───────────────────────────────────────────────────


class PromptVersionModel(Base, TimestampMixin):
    """Versioned prompt template used by the agent/LLM pipeline.

    Tracks every prompt version so that learning outcomes can be
    attributed to the exact prompt that produced them.
    """

    __tablename__ = "prompt_versions"
    __table_args__ = (
        UniqueConstraint(
            "prompt_key", "version",
            name="uq_prompt_versions_key_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Identity
    prompt_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    # Content
    template: Mapped[str] = mapped_column(Text, nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)

    # Metadata
    description: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    assignments: Mapped[list[PromptAssignmentModel]] = relationship(
        back_populates="prompt_version",
    )


# ── PromptAssignment ────────────────────────────────────────────────


class PromptAssignmentModel(Base, TimestampMixin, TenantMixin):
    """Maps a prompt version to a tenant — enables per-tenant prompt rollout.

    A tenant may be assigned a specific prompt version for A/B testing
    or gradual rollout.  If no assignment exists, the latest active
    version for the prompt_key is used.
    """

    __tablename__ = "prompt_assignments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "prompt_key",
            name="uq_prompt_assignments_tenant_key",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    prompt_key: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("prompt_versions.id"), nullable=False
    )

    # Why this assignment exists
    reason: Mapped[str] = mapped_column(String(255), default="")
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships
    prompt_version: Mapped[PromptVersionModel] = relationship(
        back_populates="assignments",
    )


# ── EvaluationRun ───────────────────────────────────────────────────


class EvaluationRunModel(Base, TimestampMixin, TenantMixin):
    """A single evaluation run — measures learning system accuracy.

    An evaluation run compares past predictions/rankings against
    actual observed outcomes over a defined time window.
    """

    __tablename__ = "evaluation_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )

    # What was evaluated
    evaluation_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "ranking", "timing", "content"
    scope: Mapped[str] = mapped_column(String(50), default="tenant")  # "tenant", "global", "cohort"

    # Time window
    window_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Aggregate metrics
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank_correlation: Mapped[float | None] = mapped_column(Float, nullable=True)
    top_k_precision: Mapped[float | None] = mapped_column(Float, nullable=True)
    top_k: Mapped[int] = mapped_column(Integer, default=3)

    # Full result payload
    details: Mapped[dict] = mapped_column(JSON, default=dict)

    # State
    status: Mapped[str] = mapped_column(String(20), default="completed")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    results: Mapped[list[EvaluationResultModel]] = relationship(
        back_populates="evaluation_run",
        cascade="all, delete-orphan",
    )


# ── EvaluationResult ────────────────────────────────────────────────


class EvaluationResultModel(Base, TimestampMixin):
    """Individual prediction-vs-actual comparison within an evaluation run."""

    __tablename__ = "evaluation_results"
    __table_args__ = (
        Index("ix_evaluation_results_run", "evaluation_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), nullable=False
    )

    # What was predicted
    action_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    action_type: Mapped[str] = mapped_column(String(50), default="")
    predicted_score: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # What actually happened
    actual_reward: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Error
    absolute_error: Mapped[float | None] = mapped_column(Float, nullable=True)

    # The explanation that was given at prediction time
    explanation: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Relationships
    evaluation_run: Mapped[EvaluationRunModel] = relationship(
        back_populates="results",
    )


# ── LearningAuditLog ───────────────────────────────────────────────


class LearningAuditLogModel(Base, TimestampMixin, TenantMixin):
    """Audit trail for learning system decisions.

    Every learning action (observation recorded, pattern derived,
    ranking produced, prompt assigned) gets an audit entry.
    Separate from the general AuditLogModel to avoid polluting
    the main audit stream.
    """

    __tablename__ = "learning_audit_log"
    __table_args__ = (
        Index(
            "ix_learning_audit_log_tenant_action",
            "tenant_id", "action",
        ),
        Index(
            "ix_learning_audit_log_tenant_entity",
            "tenant_id", "entity_type", "entity_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )

    # What happened
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), default="")
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    # Who
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    # Details
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Context
    schema_version: Mapped[int] = mapped_column(Integer, default=1)

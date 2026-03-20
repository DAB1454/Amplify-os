"""Learning subsystem configuration.

LearningConfig is the central config object.  Apps read settings from
environment variables (via pydantic-settings in their own Settings class)
and construct a LearningConfig to pass into learning components.

Environment-specific presets are provided via classmethods.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PrivacyConfig:
    """Controls what data crosses tenant boundaries."""

    # Minimum tenants whose data must contribute to a global prior
    # before it is surfaced.  Prevents single-tenant fingerprinting.
    k_anonymity_threshold: int = 5

    # When building global priors, strip these fields from observations.
    redacted_fields: tuple[str, ...] = (
        "artist_name",
        "tenant_id",
        "campaign_name",
        "content_text",
    )

    # Maximum cardinality of any categorical feature in global priors.
    # High-cardinality categoricals risk re-identification.
    max_categorical_cardinality: int = 50

    # Whether to allow cross-tenant learning at all.
    cross_tenant_enabled: bool = True


@dataclass(frozen=True)
class RewardConfig:
    """Weights and thresholds for the reward signal."""

    # Outcome weights — how much each observed outcome contributes to the
    # composite reward score for a post or campaign action.
    engagement_weight: float = 0.4
    reach_weight: float = 0.2
    save_rate_weight: float = 0.2
    click_through_weight: float = 0.2

    # Minimum observations before a reward estimate is considered reliable.
    min_observations: int = 10

    # Decay half-life in days — older observations are down-weighted.
    decay_half_life_days: float = 30.0


@dataclass(frozen=True)
class MemoryConfig:
    """Controls tenant-level memory retention."""

    # Maximum observations stored per tenant.  Oldest are evicted first.
    max_observations_per_tenant: int = 10_000

    # Observations older than this (days) are eligible for compaction.
    compaction_age_days: int = 90

    # How many top-level insights to maintain per tenant.
    max_insights_per_tenant: int = 200


@dataclass(frozen=True)
class RankingConfig:
    """Controls the ranking / recommendation layer."""

    # How many candidate actions to score in a single ranking call.
    max_candidates: int = 50

    # Blend weight between tenant-specific score and global prior.
    # 1.0 = fully tenant-specific, 0.0 = fully global prior.
    # Automatically adjusts toward tenant-specific as observations grow.
    tenant_prior_blend: float = 0.3

    # Minimum tenant observations before tenant-specific scores are used.
    # Below this, ranking relies entirely on global priors.
    cold_start_threshold: int = 20

    # Exploration rate — fraction of recommendations that are exploratory
    # rather than exploitative, to gather new signal.
    exploration_rate: float = 0.1


@dataclass(frozen=True)
class LearningConfig:
    """Top-level learning subsystem configuration.

    Construct via classmethods for environment presets, or directly for
    custom configurations.
    """

    enabled: bool = True
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    rewards: RewardConfig = field(default_factory=RewardConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    ranking: RankingConfig = field(default_factory=RankingConfig)

    # Audit every learning decision to the audit log.
    audit_enabled: bool = True

    # Log human-readable explanations alongside every score/ranking.
    explanations_enabled: bool = True

    @classmethod
    def local(cls) -> LearningConfig:
        """Preset for local development — learning enabled, relaxed thresholds."""
        return cls(
            privacy=PrivacyConfig(
                k_anonymity_threshold=1,  # no k-anon enforcement locally
                cross_tenant_enabled=True,
            ),
            rewards=RewardConfig(min_observations=1),
            memory=MemoryConfig(
                max_observations_per_tenant=1_000,
                max_insights_per_tenant=50,
            ),
            ranking=RankingConfig(
                cold_start_threshold=1,
                exploration_rate=0.2,  # explore more in dev
            ),
        )

    @classmethod
    def staging(cls) -> LearningConfig:
        """Preset for staging — production-like but with lower volume limits."""
        return cls(
            privacy=PrivacyConfig(k_anonymity_threshold=3),
            memory=MemoryConfig(max_observations_per_tenant=5_000),
        )

    @classmethod
    def production(cls) -> LearningConfig:
        """Preset for production — full privacy enforcement, defaults."""
        return cls()

    @classmethod
    def disabled(cls) -> LearningConfig:
        """Learning fully disabled — all components become no-ops."""
        return cls(enabled=False)

    @classmethod
    def for_environment(cls, deployment_mode: str) -> LearningConfig:
        """Return the preset matching the deployment_mode string."""
        presets = {
            "local": cls.local,
            "staging": cls.staging,
            "production": cls.production,
        }
        factory = presets.get(deployment_mode, cls.production)
        return factory()

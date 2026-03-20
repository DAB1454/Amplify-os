"""Feature extraction pipeline — composes extractors into a single feature vector.

The pipeline is a pure function: (PostContext) → dict of features.
It has no DB access — the caller is responsible for loading entities and
passing them in as a PostContext.  This keeps the pipeline testable and
usable from both live API calls and historical backfill scripts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from amplify.learning.feature_extraction.extractors import (
    extract_asset_features,
    extract_campaign_features,
    extract_channel_features,
    extract_cohort_features,
    extract_content_features,
    extract_early_metrics_features,
    extract_policy_features,
    extract_release_features,
    extract_sequence_features,
    extract_tenant_features,
    extract_timing_features,
    extract_track_features,
)

SCHEMA_VERSION = 1


@dataclass
class AssetContext:
    """Metadata about the primary asset attached to a post."""

    asset_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    file_size_bytes: int | None = None
    mime_type: str | None = None
    metadata: dict | None = None


@dataclass
class ChannelContext:
    """Metadata about the channel the post is published through."""

    platform: str = ""
    integration_mode: str | None = None
    account_type: str | None = None
    capabilities: dict | None = None


@dataclass
class CampaignContext:
    """Campaign and release scheduling context."""

    phase: str | None = None
    objective: str | None = None
    goals: dict | None = None
    release_date: date | None = None
    release_type: str | None = None
    track_count: int | None = None
    has_upc: bool | None = None


@dataclass
class ReleaseContext:
    """Release metadata."""

    genre: str | None = None
    track_isrc: str | None = None
    track_title: str | None = None
    track_number: int | None = None


@dataclass
class TenantContext:
    """Tenant profile context."""

    plan_tier: str | None = None
    onboarding_completed: bool | None = None
    settings: dict | None = None


@dataclass
class SequenceContext:
    """Posting sequence context."""

    position: int | None = None
    total_in_campaign: int | None = None
    prior_post_count_24h: int | None = None


@dataclass
class PolicyContext:
    """Policy and approval context."""

    policy_decision: str | None = None
    approval_required: bool | None = None
    prompt_version_id: str | None = None


@dataclass
class EarlyMetricsContext:
    """Early performance metrics (populated after publish)."""

    impressions: int | None = None
    engagements: int | None = None
    reach: int | None = None
    saves: int | None = None
    measurement_window: str | None = None


@dataclass
class PostContext:
    """Complete context for feature extraction.

    Aggregates all information needed to produce a feature vector.
    The API service populates this by loading related entities from the DB.
    Backfill scripts populate it from historical snapshots.
    """

    # Post content
    content_text: str = ""
    media_urls: list[str] = field(default_factory=list)
    media_type: str | None = None

    # Timestamps
    published_at: datetime | None = None
    scheduled_at: datetime | None = None

    # Related contexts — all optional for partial extraction
    asset: AssetContext | None = None
    channel: ChannelContext | None = None
    campaign: CampaignContext | None = None
    release: ReleaseContext | None = None
    tenant: TenantContext | None = None
    sequence: SequenceContext | None = None
    policy: PolicyContext | None = None
    early_metrics: EarlyMetricsContext | None = None
    cohort_tags: list[str] | None = None


def extract_features(ctx: PostContext) -> dict[str, Any]:
    """Run the full extraction pipeline and return a merged feature dict.

    Each extractor handles missing data gracefully — if a context block
    is None or fields are missing, those features are simply omitted.
    This allows partial extraction for both live and backfill scenarios.
    """
    features: dict[str, Any] = {}

    # Timing
    features.update(extract_timing_features(
        published_at=ctx.published_at,
        scheduled_at=ctx.scheduled_at,
    ))

    # Content
    features.update(extract_content_features(
        content_text=ctx.content_text,
        media_urls=ctx.media_urls,
        media_type=ctx.media_type,
    ))

    # Asset
    if ctx.asset:
        features.update(extract_asset_features(
            asset_type=ctx.asset.asset_type,
            width=ctx.asset.width,
            height=ctx.asset.height,
            duration_seconds=ctx.asset.duration_seconds,
            file_size_bytes=ctx.asset.file_size_bytes,
            mime_type=ctx.asset.mime_type,
            metadata=ctx.asset.metadata,
        ))

    # Channel
    if ctx.channel:
        features.update(extract_channel_features(
            platform=ctx.channel.platform,
            integration_mode=ctx.channel.integration_mode,
            account_type=ctx.channel.account_type,
            capabilities=ctx.channel.capabilities,
        ))

    # Campaign + release phase
    if ctx.campaign:
        publish_date = None
        ts = ctx.published_at or ctx.scheduled_at
        if ts:
            publish_date = ts.date() if isinstance(ts, datetime) else ts
        features.update(extract_campaign_features(
            campaign_phase=ctx.campaign.phase,
            campaign_objective=ctx.campaign.objective,
            campaign_goals=ctx.campaign.goals,
            release_date=ctx.campaign.release_date,
            publish_date=publish_date,
        ))
        features.update(extract_release_features(
            release_type=ctx.campaign.release_type,
            track_count=ctx.campaign.track_count,
            has_upc=ctx.campaign.has_upc,
        ))

    # Release / artist / track
    if ctx.release:
        features.update(extract_release_features(
            genre=ctx.release.genre,
        ))
        features.update(extract_track_features(
            track_isrc=ctx.release.track_isrc,
            track_title=ctx.release.track_title,
            track_number=ctx.release.track_number,
        ))

    # Tenant
    if ctx.tenant:
        features.update(extract_tenant_features(
            plan_tier=ctx.tenant.plan_tier,
            onboarding_completed=ctx.tenant.onboarding_completed,
            settings=ctx.tenant.settings,
        ))

    # Sequence
    if ctx.sequence:
        features.update(extract_sequence_features(
            sequence_position=ctx.sequence.position,
            total_in_campaign=ctx.sequence.total_in_campaign,
            prior_post_count_24h=ctx.sequence.prior_post_count_24h,
        ))

    # Policy
    if ctx.policy:
        features.update(extract_policy_features(
            policy_decision=ctx.policy.policy_decision,
            approval_required=ctx.policy.approval_required,
            prompt_version_id=ctx.policy.prompt_version_id,
        ))

    # Early metrics
    if ctx.early_metrics:
        features.update(extract_early_metrics_features(
            impressions=ctx.early_metrics.impressions,
            engagements=ctx.early_metrics.engagements,
            reach=ctx.early_metrics.reach,
            saves=ctx.early_metrics.saves,
            measurement_window=ctx.early_metrics.measurement_window,
        ))

    # Cohort tags
    if ctx.cohort_tags:
        features.update(extract_cohort_features(cohort_tags=ctx.cohort_tags))

    # Stamp schema version
    features["_schema_version"] = SCHEMA_VERSION

    return features

"""Feature extraction — converts raw actions/posts into feature vectors.

Extractors are pure functions: (raw data) → dict of features.
Each extractor is registered in the FeatureRegistry so that downstream
components know what features to expect.

The pipeline module composes all extractors into a single call:
  PostContext → dict[str, Any]
"""

from amplify.learning.feature_extraction.registry import FeatureRegistry
from amplify.learning.feature_extraction.extractors import (
    extract_asset_features,
    extract_campaign_features,
    extract_channel_features,
    extract_cohort_features,
    extract_content_features,
    extract_early_metrics_features,
    extract_platform_features,
    extract_policy_features,
    extract_release_features,
    extract_sequence_features,
    extract_tenant_features,
    extract_timing_features,
    extract_track_features,
)
from amplify.learning.feature_extraction.pipeline import (
    AssetContext,
    CampaignContext,
    ChannelContext,
    EarlyMetricsContext,
    PolicyContext,
    PostContext,
    ReleaseContext,
    SequenceContext,
    TenantContext,
    extract_features,
)

__all__ = [
    "FeatureRegistry",
    # Individual extractors
    "extract_asset_features",
    "extract_campaign_features",
    "extract_channel_features",
    "extract_cohort_features",
    "extract_content_features",
    "extract_early_metrics_features",
    "extract_platform_features",
    "extract_policy_features",
    "extract_release_features",
    "extract_sequence_features",
    "extract_tenant_features",
    "extract_timing_features",
    "extract_track_features",
    # Pipeline
    "AssetContext",
    "CampaignContext",
    "ChannelContext",
    "EarlyMetricsContext",
    "PolicyContext",
    "PostContext",
    "ReleaseContext",
    "SequenceContext",
    "TenantContext",
    "extract_features",
]

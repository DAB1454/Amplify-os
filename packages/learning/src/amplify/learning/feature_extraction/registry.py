"""Feature registry — central catalog of all known features.

The registry serves two purposes:
1. Documentation: what features exist, what they mean, what values are valid.
2. Validation: warn when observations contain unknown features.
"""

from __future__ import annotations

from amplify.learning.schemas.feature import (
    FeatureDefinition,
    FeatureSchema,
    FeatureType,
)


def _default_schema() -> FeatureSchema:
    """Build the default feature schema with all standard features."""
    schema = FeatureSchema()

    # ── Timing ────────────────────────────────────────────────────
    schema.register(FeatureDefinition(
        name="post_hour",
        feature_type=FeatureType.TEMPORAL,
        description="Hour of day the post was published (0-23, UTC).",
        min_value=0,
        max_value=23,
    ))
    schema.register(FeatureDefinition(
        name="post_day_of_week",
        feature_type=FeatureType.TEMPORAL,
        description="Day of week (0=Monday, 6=Sunday).",
        min_value=0,
        max_value=6,
    ))
    schema.register(FeatureDefinition(
        name="timing_day_part",
        feature_type=FeatureType.CATEGORICAL,
        description="Part of day: morning, afternoon, evening, night.",
        categories=["morning", "afternoon", "evening", "night"],
    ))
    schema.register(FeatureDefinition(
        name="timing_is_weekend",
        feature_type=FeatureType.BOOLEAN,
        description="Whether the post was published on a weekend.",
    ))

    # ── Content ───────────────────────────────────────────────────
    schema.register(FeatureDefinition(
        name="content_type",
        feature_type=FeatureType.CATEGORICAL,
        description="Type of content: image, video, carousel, text, story, reel.",
        categories=["image", "video", "carousel", "text", "story", "reel"],
    ))
    schema.register(FeatureDefinition(
        name="caption_length",
        feature_type=FeatureType.NUMERIC,
        description="Character count of the post caption/text.",
        min_value=0,
    ))
    schema.register(FeatureDefinition(
        name="caption_length_bucket",
        feature_type=FeatureType.CATEGORICAL,
        description="Bucketed caption length: empty, micro, short, medium, long, very_long.",
        categories=["empty", "micro", "short", "medium", "long", "very_long"],
    ))
    schema.register(FeatureDefinition(
        name="hashtag_count",
        feature_type=FeatureType.NUMERIC,
        description="Number of hashtags in the post.",
        min_value=0,
    ))
    schema.register(FeatureDefinition(
        name="mention_count",
        feature_type=FeatureType.NUMERIC,
        description="Number of @mentions in the post.",
        min_value=0,
    ))
    schema.register(FeatureDefinition(
        name="emoji_count",
        feature_type=FeatureType.NUMERIC,
        description="Number of emoji characters in the post.",
        min_value=0,
    ))
    schema.register(FeatureDefinition(
        name="has_link",
        feature_type=FeatureType.BOOLEAN,
        description="Whether the post contains an external link.",
    ))
    schema.register(FeatureDefinition(
        name="media_count",
        feature_type=FeatureType.NUMERIC,
        description="Number of media items attached to the post.",
        min_value=0,
    ))
    schema.register(FeatureDefinition(
        name="cta_type",
        feature_type=FeatureType.CATEGORICAL,
        description="Primary call-to-action type detected in caption.",
        categories=[
            "presave", "stream", "watch", "link_in_bio", "swipe_up",
            "shop", "follow", "comment", "share",
        ],
    ))
    schema.register(FeatureDefinition(
        name="hook_family",
        feature_type=FeatureType.CATEGORICAL,
        description="Opening hook style of the caption.",
        categories=[
            "question", "announcement", "emoji_lead", "quote",
            "number", "direct_address", "statement",
        ],
    ))

    # ── Asset ─────────────────────────────────────────────────────
    schema.register(FeatureDefinition(
        name="asset_type",
        feature_type=FeatureType.CATEGORICAL,
        description="Type of primary asset: image, video, audio.",
        categories=["image", "video", "audio"],
    ))
    schema.register(FeatureDefinition(
        name="asset_aspect_ratio",
        feature_type=FeatureType.NUMERIC,
        description="Width/height ratio of the primary asset.",
        min_value=0,
    ))
    schema.register(FeatureDefinition(
        name="asset_orientation",
        feature_type=FeatureType.CATEGORICAL,
        description="Orientation: portrait, landscape, square.",
        categories=["portrait", "landscape", "square"],
    ))
    schema.register(FeatureDefinition(
        name="asset_video_duration",
        feature_type=FeatureType.NUMERIC,
        description="Duration of video asset in seconds.",
        min_value=0,
    ))
    schema.register(FeatureDefinition(
        name="asset_video_duration_bucket",
        feature_type=FeatureType.CATEGORICAL,
        description="Bucketed video duration.",
        categories=["micro", "short", "medium", "standard", "long", "very_long"],
    ))
    schema.register(FeatureDefinition(
        name="asset_file_size_mb",
        feature_type=FeatureType.NUMERIC,
        description="File size of primary asset in megabytes.",
        min_value=0,
    ))

    # ── Channel ───────────────────────────────────────────────────
    schema.register(FeatureDefinition(
        name="platform",
        feature_type=FeatureType.CATEGORICAL,
        description="Social media platform.",
        categories=["instagram", "youtube", "tiktok", "twitter", "facebook"],
    ))
    schema.register(FeatureDefinition(
        name="channel_integration_mode",
        feature_type=FeatureType.CATEGORICAL,
        description="How the channel is connected: automatic or assisted.",
        categories=["automatic", "assisted"],
    ))
    schema.register(FeatureDefinition(
        name="channel_account_type",
        feature_type=FeatureType.CATEGORICAL,
        description="Platform account type: professional, creator, personal.",
        categories=["professional", "creator", "personal"],
    ))
    schema.register(FeatureDefinition(
        name="channel_can_publish",
        feature_type=FeatureType.BOOLEAN,
        description="Whether the channel supports automated publishing.",
    ))

    # ── Campaign ──────────────────────────────────────────────────
    schema.register(FeatureDefinition(
        name="campaign_phase",
        feature_type=FeatureType.CATEGORICAL,
        description="Current phase of the parent campaign.",
        categories=["pre_release", "release_week", "sustain", "evergreen"],
    ))
    schema.register(FeatureDefinition(
        name="campaign_objective",
        feature_type=FeatureType.CATEGORICAL,
        description="Primary campaign objective.",
        categories=["streams", "presaves", "followers", "awareness", "engagement"],
    ))

    # ── Release phase ─────────────────────────────────────────────
    schema.register(FeatureDefinition(
        name="release_phase",
        feature_type=FeatureType.CATEGORICAL,
        description="Where the post falls relative to the release date.",
        categories=[
            "early_announce", "pre_release", "countdown", "release_day",
            "release_week", "post_release", "sustain", "evergreen",
        ],
    ))
    schema.register(FeatureDefinition(
        name="release_days_offset",
        feature_type=FeatureType.NUMERIC,
        description="Days between publish date and release date (negative = before release).",
    ))
    schema.register(FeatureDefinition(
        name="release_type",
        feature_type=FeatureType.CATEGORICAL,
        description="Type of release: single, ep, album.",
        categories=["single", "ep", "album"],
    ))
    schema.register(FeatureDefinition(
        name="release_track_count",
        feature_type=FeatureType.NUMERIC,
        description="Number of tracks on the release.",
        min_value=1,
    ))
    schema.register(FeatureDefinition(
        name="release_has_upc",
        feature_type=FeatureType.BOOLEAN,
        description="Whether the release has a UPC code (indicates distribution).",
    ))

    # ── Artist ────────────────────────────────────────────────────
    schema.register(FeatureDefinition(
        name="artist_genre",
        feature_type=FeatureType.CATEGORICAL,
        description="Primary genre of the artist.",
    ))

    # ── Tenant ────────────────────────────────────────────────────
    schema.register(FeatureDefinition(
        name="tenant_plan_tier",
        feature_type=FeatureType.CATEGORICAL,
        description="Tenant subscription tier.",
        categories=["solo", "team", "label"],
    ))
    schema.register(FeatureDefinition(
        name="tenant_onboarding_completed",
        feature_type=FeatureType.BOOLEAN,
        description="Whether the tenant has completed onboarding.",
    ))

    # ── Sequence ──────────────────────────────────────────────────
    schema.register(FeatureDefinition(
        name="sequence_position",
        feature_type=FeatureType.NUMERIC,
        description="Position of this post within its campaign sequence (1-indexed).",
        min_value=1,
    ))
    schema.register(FeatureDefinition(
        name="campaign_total_posts",
        feature_type=FeatureType.NUMERIC,
        description="Total posts planned or created in the campaign.",
        min_value=0,
    ))
    schema.register(FeatureDefinition(
        name="sequence_progress",
        feature_type=FeatureType.NUMERIC,
        description="Fraction of campaign posts completed (0.0-1.0).",
        min_value=0,
        max_value=1,
    ))
    schema.register(FeatureDefinition(
        name="posts_in_last_24h",
        feature_type=FeatureType.NUMERIC,
        description="Number of posts published in the 24 hours prior to this post.",
        min_value=0,
    ))

    # ── Policy & approval ─────────────────────────────────────────
    schema.register(FeatureDefinition(
        name="policy_decision",
        feature_type=FeatureType.CATEGORICAL,
        description="Policy engine decision for this post.",
        categories=["allow", "require_approval", "block"],
    ))
    schema.register(FeatureDefinition(
        name="approval_required",
        feature_type=FeatureType.BOOLEAN,
        description="Whether the post required human approval.",
    ))
    schema.register(FeatureDefinition(
        name="prompt_version_id",
        feature_type=FeatureType.CATEGORICAL,
        description="ID of the prompt version used to generate this post.",
    ))

    # ── Early metrics ─────────────────────────────────────────────
    schema.register(FeatureDefinition(
        name="metrics_window",
        feature_type=FeatureType.CATEGORICAL,
        description="Measurement window for early metrics.",
        categories=["1h", "3h", "6h", "12h", "24h"],
    ))
    schema.register(FeatureDefinition(
        name="early_impressions_log",
        feature_type=FeatureType.NUMERIC,
        description="log(1 + impressions) for early performance signal.",
        min_value=0,
    ))
    schema.register(FeatureDefinition(
        name="early_reach_log",
        feature_type=FeatureType.NUMERIC,
        description="log(1 + reach) for early performance signal.",
        min_value=0,
    ))
    schema.register(FeatureDefinition(
        name="early_engagement_rate",
        feature_type=FeatureType.NUMERIC,
        description="Early engagement rate (engagements / impressions).",
        min_value=0,
        max_value=1,
    ))
    schema.register(FeatureDefinition(
        name="early_save_rate",
        feature_type=FeatureType.NUMERIC,
        description="Early save rate (saves / reach).",
        min_value=0,
        max_value=1,
    ))

    # ── Track ─────────────────────────────────────────────────────
    schema.register(FeatureDefinition(
        name="track_isrc",
        feature_type=FeatureType.CATEGORICAL,
        description="ISRC identifier of the promoted track.",
    ))
    schema.register(FeatureDefinition(
        name="track_has_title",
        feature_type=FeatureType.BOOLEAN,
        description="Whether a specific track title is associated.",
    ))
    schema.register(FeatureDefinition(
        name="track_number",
        feature_type=FeatureType.NUMERIC,
        description="Track number on the release.",
        min_value=1,
    ))

    # ── Cohort ────────────────────────────────────────────────────
    schema.register(FeatureDefinition(
        name="cohort_tags",
        feature_type=FeatureType.CATEGORICAL,
        description="List of cohort membership tags for the tenant/artist.",
    ))

    return schema


class FeatureRegistry:
    """Singleton-ish access to the global feature schema.

    Components use FeatureRegistry.schema() to get the current schema
    for validation and documentation.
    """

    _schema: FeatureSchema | None = None

    @classmethod
    def schema(cls) -> FeatureSchema:
        if cls._schema is None:
            cls._schema = _default_schema()
        return cls._schema

    @classmethod
    def reset(cls) -> None:
        """Reset to defaults (useful in tests)."""
        cls._schema = None

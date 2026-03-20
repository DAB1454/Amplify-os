"""Built-in feature extractors.

Each extractor is a pure function: (raw data) → dict of features.
They are composable — call several and merge the results into one
FeatureVector for an observation.

Feature naming conventions:
- Prefix by domain: timing_, content_, asset_, channel_, campaign_, release_, tenant_
- Boolean features: has_*, is_*
- Bucketed features: *_bucket
- All values are JSON-serializable primitives (str, int, float, bool, None)
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from typing import Any


# ── Timing ────────────────────────────────────────────────────────


def extract_timing_features(
    published_at: datetime | None = None,
    scheduled_at: datetime | None = None,
) -> dict[str, Any]:
    """Extract time-of-day and day-of-week features from a timestamp."""
    ts = published_at or scheduled_at
    if ts is None:
        return {}

    hour = ts.hour
    return {
        "post_hour": hour,
        "post_day_of_week": ts.weekday(),
        "timing_day_part": _day_part(hour),
        "timing_is_weekend": ts.weekday() >= 5,
    }


def _day_part(hour: int) -> str:
    """Classify hour into human-readable day part."""
    if hour < 6:
        return "night"
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 21:
        return "evening"
    return "night"


# ── Content ───────────────────────────────────────────────────────

# CTA patterns — order matters, first match wins
_CTA_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("presave", re.compile(r"\bpre[\-\s]?save\b", re.I)),
    ("stream", re.compile(r"\b(stream|listen)\s+(now|here|on|everywhere)\b", re.I)),
    ("watch", re.compile(r"\bwatch\s+(now|the|here|on)\b", re.I)),
    ("link_in_bio", re.compile(r"\blink\s+in\s+(bio|profile)\b", re.I)),
    ("swipe_up", re.compile(r"\bswipe\s+up\b", re.I)),
    ("shop", re.compile(r"\b(shop|buy|order)\s+(now|here|the)\b", re.I)),
    ("follow", re.compile(r"\bfollow\s+(us|me|@)\b", re.I)),
    ("comment", re.compile(r"\b(comment|drop|tag)\s+(below|your|a\s+friend)\b", re.I)),
    ("share", re.compile(r"\bshare\s+(this|with)\b", re.I)),
]

_CAPTION_BUCKETS = [
    (0, "empty"),
    (50, "micro"),
    (150, "short"),
    (300, "medium"),
    (600, "long"),
    (float("inf"), "very_long"),
]


def extract_content_features(
    content_text: str = "",
    media_urls: list[str] | None = None,
    media_type: str | None = None,
) -> dict[str, Any]:
    """Extract content-shape features from post data."""
    features: dict[str, Any] = {}

    # Content type
    if media_type:
        features["content_type"] = media_type
    elif media_urls:
        features["content_type"] = "carousel" if len(media_urls) > 1 else "image"
    elif content_text:
        features["content_type"] = "text"

    # Caption metrics
    caption_len = len(content_text)
    features["caption_length"] = caption_len
    features["caption_length_bucket"] = _bucket(caption_len, _CAPTION_BUCKETS)
    features["hashtag_count"] = content_text.count("#")
    features["mention_count"] = content_text.count("@") - content_text.count("@@")
    features["emoji_count"] = _count_emoji(content_text)
    features["has_link"] = "http://" in content_text or "https://" in content_text
    features["media_count"] = len(media_urls) if media_urls else 0

    # CTA detection
    features["cta_type"] = _detect_cta(content_text)

    # Hook — first line/sentence of caption
    features["hook_family"] = _classify_hook(content_text)

    return features


def _bucket(value: float, buckets: list[tuple[float, str]]) -> str:
    for threshold, label in buckets:
        if value <= threshold:
            return label
    return buckets[-1][1]


def _count_emoji(text: str) -> int:
    """Count emoji characters (rough heuristic, no heavy dependency)."""
    count = 0
    for ch in text:
        cp = ord(ch)
        if (0x1F600 <= cp <= 0x1F64F or  # emoticons
            0x1F300 <= cp <= 0x1F5FF or   # misc symbols
            0x1F680 <= cp <= 0x1F6FF or   # transport
            0x1F900 <= cp <= 0x1F9FF or   # supplemental
            0x2600 <= cp <= 0x26FF or     # misc symbols
            0x2700 <= cp <= 0x27BF):      # dingbats
            count += 1
    return count


def _detect_cta(text: str) -> str | None:
    """Detect the primary call-to-action type in caption text."""
    for cta_type, pattern in _CTA_PATTERNS:
        if pattern.search(text):
            return cta_type
    return None


def _classify_hook(text: str) -> str | None:
    """Classify the opening hook of a caption into a family.

    Hook families represent engagement patterns:
    - question: opens with a question
    - announcement: opens with news/announcement language
    - emoji_lead: opens with emoji
    - quote: opens with a quotation
    - number: opens with a number/statistic
    - direct_address: opens with "you" language
    """
    if not text:
        return None

    first_line = text.split("\n")[0].strip()
    if not first_line:
        return None

    if first_line.endswith("?"):
        return "question"
    if re.match(r"^\d", first_line):
        return "number"
    if re.match(r'^["\u201c\u201d\u2018\u2019]', first_line):
        return "quote"
    if ord(first_line[0]) > 0x2000:
        return "emoji_lead"
    low = first_line.lower()
    if low.startswith(("new ", "introducing ", "announcing ", "just dropped", "out now")):
        return "announcement"
    if low.startswith(("you ", "your ", "have you")):
        return "direct_address"

    return "statement"


# ── Asset ─────────────────────────────────────────────────────────


def extract_asset_features(
    asset_type: str | None = None,
    width: int | None = None,
    height: int | None = None,
    duration_seconds: float | None = None,
    file_size_bytes: int | None = None,
    mime_type: str | None = None,
    metadata: dict | None = None,
) -> dict[str, Any]:
    """Extract features from asset metadata."""
    features: dict[str, Any] = {}

    if asset_type:
        features["asset_type"] = asset_type

    # Aspect ratio
    if width and height and height > 0:
        ratio = width / height
        features["asset_aspect_ratio"] = round(ratio, 2)
        features["asset_orientation"] = _orientation(ratio)

    # Video duration
    if duration_seconds is not None:
        features["asset_video_duration"] = round(duration_seconds, 1)
        features["asset_video_duration_bucket"] = _bucket(
            duration_seconds,
            [
                (3, "micro"),     # < 3s
                (15, "short"),    # 3-15s
                (30, "medium"),   # 15-30s
                (60, "standard"), # 30-60s
                (180, "long"),    # 1-3min
                (float("inf"), "very_long"),
            ],
        )

    if file_size_bytes is not None:
        features["asset_file_size_mb"] = round(file_size_bytes / (1024 * 1024), 2)

    return features


def _orientation(ratio: float) -> str:
    if ratio > 1.1:
        return "landscape"
    if ratio < 0.9:
        return "portrait"
    return "square"


# ── Channel ───────────────────────────────────────────────────────


def extract_channel_features(
    platform: str = "",
    integration_mode: str | None = None,
    account_type: str | None = None,
    capabilities: dict | None = None,
) -> dict[str, Any]:
    """Extract features from the channel connection."""
    features: dict[str, Any] = {}

    if platform:
        features["platform"] = platform
    if integration_mode:
        features["channel_integration_mode"] = integration_mode
    if account_type:
        features["channel_account_type"] = account_type
    if capabilities:
        features["channel_can_publish"] = capabilities.get("can_publish", False)

    return features


# ── Campaign & Release ────────────────────────────────────────────


def extract_campaign_features(
    campaign_phase: str | None = None,
    campaign_objective: str | None = None,
    campaign_goals: dict | None = None,
    release_date: date | None = None,
    publish_date: date | None = None,
) -> dict[str, Any]:
    """Extract features from the campaign and release context."""
    features: dict[str, Any] = {}

    if campaign_phase:
        features["campaign_phase"] = campaign_phase

    # Derive campaign objective from goals if not explicit
    if campaign_objective:
        features["campaign_objective"] = campaign_objective
    elif campaign_goals:
        # Infer primary objective from goal keys
        objective_priority = ["streams", "presaves", "followers", "awareness", "engagement"]
        for obj in objective_priority:
            if obj in campaign_goals:
                features["campaign_objective"] = obj
                break

    # Release phase derived from dates
    if release_date and publish_date:
        features["release_phase"] = _compute_release_phase(release_date, publish_date)
        features["release_days_offset"] = (publish_date - release_date).days

    return features


def _compute_release_phase(release_date: date, publish_date: date) -> str:
    """Classify where a post falls relative to the release date."""
    delta = (publish_date - release_date).days
    if delta < -14:
        return "early_announce"
    if delta < -3:
        return "pre_release"
    if delta < 0:
        return "countdown"
    if delta == 0:
        return "release_day"
    if delta <= 3:
        return "release_week"
    if delta <= 14:
        return "post_release"
    if delta <= 60:
        return "sustain"
    return "evergreen"


# ── Release metadata ──────────────────────────────────────────────


def extract_release_features(
    release_type: str | None = None,
    track_count: int | None = None,
    has_upc: bool | None = None,
    genre: str | None = None,
) -> dict[str, Any]:
    """Extract features from release metadata."""
    features: dict[str, Any] = {}

    if release_type:
        features["release_type"] = release_type
    if track_count is not None:
        features["release_track_count"] = track_count
    if has_upc is not None:
        features["release_has_upc"] = has_upc
    if genre:
        features["artist_genre"] = genre

    return features


# ── Tenant profile ────────────────────────────────────────────────


def extract_tenant_features(
    plan_tier: str | None = None,
    onboarding_completed: bool | None = None,
    settings: dict | None = None,
) -> dict[str, Any]:
    """Extract features from the tenant profile."""
    features: dict[str, Any] = {}

    if plan_tier:
        features["tenant_plan_tier"] = plan_tier
    if onboarding_completed is not None:
        features["tenant_onboarding_completed"] = onboarding_completed

    return features


# ── Sequence position ─────────────────────────────────────────────


def extract_sequence_features(
    sequence_position: int | None = None,
    total_in_campaign: int | None = None,
    prior_post_count_24h: int | None = None,
) -> dict[str, Any]:
    """Extract posting sequence and cadence features."""
    features: dict[str, Any] = {}

    if sequence_position is not None:
        features["sequence_position"] = sequence_position
    if total_in_campaign is not None:
        features["campaign_total_posts"] = total_in_campaign
        if sequence_position is not None and total_in_campaign > 0:
            features["sequence_progress"] = round(
                sequence_position / total_in_campaign, 2
            )
    if prior_post_count_24h is not None:
        features["posts_in_last_24h"] = prior_post_count_24h

    return features


# ── Policy & approval ─────────────────────────────────────────────


def extract_policy_features(
    policy_decision: str | None = None,
    approval_required: bool | None = None,
    prompt_version_id: str | None = None,
) -> dict[str, Any]:
    """Extract features from policy evaluation and approval status."""
    features: dict[str, Any] = {}

    if policy_decision:
        features["policy_decision"] = policy_decision
    if approval_required is not None:
        features["approval_required"] = approval_required
    if prompt_version_id:
        features["prompt_version_id"] = prompt_version_id

    return features


# ── Early metrics (for enrichment after publish) ──────────────────


def extract_early_metrics_features(
    impressions: int | None = None,
    engagements: int | None = None,
    reach: int | None = None,
    saves: int | None = None,
    measurement_window: str | None = None,
) -> dict[str, Any]:
    """Extract normalized early-performance signals.

    These features are added post-publish when early metrics arrive,
    enabling models to learn from initial performance signals.
    """
    features: dict[str, Any] = {}

    if measurement_window:
        features["metrics_window"] = measurement_window

    if impressions is not None and impressions > 0:
        features["early_impressions_log"] = round(math.log1p(impressions), 3)
    if reach is not None and reach > 0:
        features["early_reach_log"] = round(math.log1p(reach), 3)
    if engagements is not None and impressions and impressions > 0:
        features["early_engagement_rate"] = round(engagements / impressions, 6)
    if saves is not None and reach and reach > 0:
        features["early_save_rate"] = round(saves / reach, 6)

    return features


# ── Track identifier ──────────────────────────────────────────────


def extract_track_features(
    track_isrc: str | None = None,
    track_title: str | None = None,
    track_number: int | None = None,
) -> dict[str, Any]:
    """Extract features from the track being promoted."""
    features: dict[str, Any] = {}

    if track_isrc:
        features["track_isrc"] = track_isrc
    if track_title:
        features["track_has_title"] = True
    if track_number is not None:
        features["track_number"] = track_number

    return features


# ── Cohort tags ───────────────────────────────────────────────────


def extract_cohort_features(
    cohort_tags: list[str] | None = None,
) -> dict[str, Any]:
    """Extract cohort membership as features."""
    if not cohort_tags:
        return {}
    return {"cohort_tags": sorted(cohort_tags)}


# ── Legacy / convenience ──────────────────────────────────────────


def extract_platform_features(
    platform: str = "",
    campaign_phase: str | None = None,
) -> dict[str, Any]:
    """Extract platform and campaign context features.

    Retained for backward compatibility — prefer extract_channel_features
    and extract_campaign_features for new code.
    """
    features: dict[str, Any] = {}
    if platform:
        features["platform"] = platform
    if campaign_phase:
        features["campaign_phase"] = campaign_phase
    return features

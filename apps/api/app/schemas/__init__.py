"""Request and response schemas for the Amplify-OS API."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

from pydantic import BaseModel, EmailStr, Field


# ── Auth ──────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = None
    tenant_name: str = Field(min_length=1, max_length=255)

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int

class RefreshRequest(BaseModel):
    refresh_token: str

class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Tenant ────────────────────────────────────────────────────────

class TenantResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    plan_tier: str
    stripe_customer_id: str | None = None
    onboarding_completed: bool = False
    settings: dict
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

class TenantUpdateRequest(BaseModel):
    name: str | None = None
    settings: dict | None = None
    onboarding_completed: bool | None = None


# ── Artist ────────────────────────────────────────────────────────

class ArtistCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    bio: str = ""
    genre: str = ""
    image_url: str | None = None
    spotify_id: str | None = None
    social_links: dict = Field(default_factory=dict)

class ArtistUpdateRequest(BaseModel):
    name: str | None = None
    bio: str | None = None
    genre: str | None = None
    image_url: str | None = None
    spotify_id: str | None = None
    social_links: dict | None = None

class ArtistResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    slug: str
    bio: str
    genre: str
    image_url: str | None
    spotify_id: str | None
    social_links: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Release ───────────────────────────────────────────────────────

DESTINATION_FIELDS = [
    "hyperfollow_url", "linktree_url", "bandcamp_url",
    "youtube_url", "tiktok_url", "instagram_url",
]

class ReleaseCreateRequest(BaseModel):
    artist_id: uuid.UUID
    title: str = Field(min_length=1, max_length=255)
    release_type: str = "single"  # single, ep, album, compilation
    release_date: date | None = None
    upc: str | None = None
    artwork_url: str | None = None
    metadata: dict = Field(default_factory=dict)
    hyperfollow_url: str | None = None
    linktree_url: str | None = None
    bandcamp_url: str | None = None
    youtube_url: str | None = None
    tiktok_url: str | None = None
    instagram_url: str | None = None

class ReleaseUpdateRequest(BaseModel):
    title: str | None = None
    release_type: str | None = None
    status: str | None = None
    release_date: date | None = None
    upc: str | None = None
    artwork_url: str | None = None
    metadata: dict | None = None
    hyperfollow_url: str | None = None
    linktree_url: str | None = None
    bandcamp_url: str | None = None
    youtube_url: str | None = None
    tiktok_url: str | None = None
    instagram_url: str | None = None

class ReleaseResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    artist_id: uuid.UUID
    title: str
    release_type: str
    status: str
    release_date: date | None
    upc: str | None
    artwork_url: str | None
    metadata_: dict = Field(alias="metadata_", serialization_alias="metadata")
    hyperfollow_url: str | None
    linktree_url: str | None
    bandcamp_url: str | None
    youtube_url: str | None
    tiktok_url: str | None
    instagram_url: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True}


# ── Track ─────────────────────────────────────────────────────────

class TrackCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    track_number: int = 1
    duration_seconds: int | None = None
    isrc: str | None = None
    audio_url: str | None = None
    lyrics: str | None = None
    is_single: bool = False

class TrackUpdateRequest(BaseModel):
    title: str | None = None
    track_number: int | None = None
    duration_seconds: int | None = None
    isrc: str | None = None
    audio_url: str | None = None
    lyrics: str | None = None
    is_single: bool | None = None

class TrackResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    release_id: uuid.UUID
    title: str
    track_number: int
    duration_seconds: int | None
    isrc: str | None
    audio_url: str | None
    lyrics: str | None
    is_single: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Campaign ──────────────────────────────────────────────────────

class CampaignCreateRequest(BaseModel):
    artist_id: uuid.UUID
    release_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=255)
    phase: str = "pre_release"
    start_date: date | None = None
    end_date: date | None = None
    budget: float | None = None
    goals: dict = Field(default_factory=dict)
    config: dict = Field(default_factory=dict)

class CampaignUpdateRequest(BaseModel):
    name: str | None = None
    status: str | None = None
    phase: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    budget: float | None = None
    goals: dict | None = None
    config: dict | None = None

class CampaignResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    artist_id: uuid.UUID
    release_id: uuid.UUID | None
    name: str
    status: str
    phase: str
    start_date: date | None
    end_date: date | None
    budget: float | None
    goals: dict
    config: dict
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Calendar Item ─────────────────────────────────────────────────

class CalendarItemCreateRequest(BaseModel):
    campaign_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    item_type: str  # post, milestone, deadline, reminder
    scheduled_date: date
    scheduled_time: time | None = None

class CalendarItemUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    item_type: str | None = None
    scheduled_date: date | None = None
    scheduled_time: time | None = None
    is_completed: bool | None = None

class CalendarItemResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    campaign_id: uuid.UUID | None
    title: str
    description: str
    item_type: str
    scheduled_date: date
    scheduled_time: time | None
    is_completed: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Post ──────────────────────────────────────────────────────────

class PostCreateRequest(BaseModel):
    campaign_id: uuid.UUID | None = None
    channel_id: uuid.UUID
    platform: str  # instagram, tiktok, youtube, etc.
    content_text: str = ""
    media_urls: list[str] = Field(default_factory=list)
    scheduled_at: datetime | None = None
    destination_url: str | None = None

class PostUpdateRequest(BaseModel):
    content_text: str | None = None
    media_urls: list[str] | None = None
    scheduled_at: datetime | None = None
    status: str | None = None
    destination_url: str | None = None

class PostResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    campaign_id: uuid.UUID | None
    channel_id: uuid.UUID
    platform: str
    status: str
    content_text: str
    media_urls: list[str]
    scheduled_at: datetime | None
    published_at: datetime | None
    platform_post_id: str | None
    permalink: str | None
    engagement: dict
    destination_url: str | None
    retry_count: int
    max_retries: int
    last_error: str | None
    policy_decision: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PostQueueResponse(BaseModel):
    post_id: str
    status: str
    policy_decision: str | None = None
    reasons: list[str] = []

class PostActionResponse(BaseModel):
    post_id: str
    status: str
    published_at: str | None = None
    permalink: str | None = None
    platform_post_id: str | None = None
    scheduled_at: str | None = None
    retry_count: int | None = None
    retry_delay_seconds: int | None = None
    error: str | None = None
    alert: bool | None = None
    preview: dict | None = None

class PostStatusResponse(BaseModel):
    post_id: str
    status: str
    platform: str | None = None
    scheduled_at: str | None = None
    published_at: str | None = None
    permalink: str | None = None
    platform_post_id: str | None = None
    retry_count: int | None = None
    max_retries: int | None = None
    last_error: str | None = None
    policy_decision: str | None = None

class ScheduleRequest(BaseModel):
    scheduled_at: datetime


# ── Approval ─────────────────────────────────────────────────────

class ApprovalCreateRequest(BaseModel):
    action_type: str  # publish_post, delete_post, budget_change, etc.
    entity_type: str = ""
    entity_id: uuid.UUID | None = None
    notes: str = ""
    action_payload: dict = Field(default_factory=dict)

class ApprovalDecisionRequest(BaseModel):
    comment: str = ""

class ApprovalResubmitRequest(BaseModel):
    notes: str = ""
    action_payload: dict | None = None

class ApprovalCommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=2000)

class ApprovalCommentResponse(BaseModel):
    id: uuid.UUID
    approval_id: uuid.UUID
    user_id: uuid.UUID | None
    body: str
    created_at: datetime

    model_config = {"from_attributes": True}

class ApprovalResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    action_type: str
    entity_type: str
    entity_id: uuid.UUID | None
    risk_level: str
    risk_reasons: list[str]
    policy_snapshot: dict
    action_payload: dict
    post_id: uuid.UUID | None
    asset_id: uuid.UUID | None
    requested_by: uuid.UUID | None
    reviewed_by: uuid.UUID | None
    status: str
    notes: str
    reviewed_at: datetime | None
    expires_at: datetime | None
    comments: list[ApprovalCommentResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

class ApprovalCheckResponse(BaseModel):
    requires_approval: bool
    risk_level: str
    reasons: list[str]

class ApprovalCountResponse(BaseModel):
    pending_count: int


# ── Calendar Import ───────────────────────────────────────────────

class CalendarImportRowError(BaseModel):
    row: int
    column: str
    value: str
    reason: str

class CalendarImportResponse(BaseModel):
    total_rows: int
    imported: int
    skipped: int
    success: bool
    errors: list[CalendarImportRowError]
    warnings: list[str]
    created_ids: list[str]


# ── Audit Log ─────────────────────────────────────────────────────

class AuditLogResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    changes: dict
    ip_address: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Common ────────────────────────────────────────────────────────

class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    offset: int
    limit: int

class MessageResponse(BaseModel):
    message: str

class ErrorResponse(BaseModel):
    detail: str


# ── Redirect / Smart Links ──────────────────────────────────────

class RedirectCreateRequest(BaseModel):
    target_url: str = Field(min_length=1)
    slug: str | None = None
    campaign_id: uuid.UUID | None = None

class RedirectResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    slug: str
    target_url: str
    campaign_id: uuid.UUID | None
    click_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Destination Validation ───────────────────────────────────────

class PostDestinationWarning(BaseModel):
    post_id: uuid.UUID
    platform: str
    status: str
    scheduled_at: datetime | None

# ── Billing ──────────────────────────────────────────────────────

class PlanResponse(BaseModel):
    id: str
    name: str
    tier: str
    price_monthly: float
    price_yearly: float
    features: list[str]
    limits: dict

class UsageItemResponse(BaseModel):
    label: str
    current: int
    limit: int
    unlimited: bool
    at_limit: bool
    pct: int

class UsageSummaryResponse(BaseModel):
    tier: str
    items: list[UsageItemResponse]

class SubscribeRequest(BaseModel):
    plan_tier: str
    billing_period: str = "monthly"  # monthly or yearly

class CheckoutResponse(BaseModel):
    checkout_url: str
    session_id: str

class PortalResponse(BaseModel):
    portal_url: str


# ── Campaign Template ────────────────────────────────────────────

class CampaignTemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""
    phase: str = "pre_release"
    goals: dict = Field(default_factory=dict)
    config: dict = Field(default_factory=dict)
    post_templates: list[dict] = Field(default_factory=list)
    is_global: bool = False
    category: str = "general"
    tags: list[str] = Field(default_factory=list)

class CampaignTemplateResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    name: str
    description: str
    phase: str
    goals: dict
    config: dict
    post_templates: list
    is_global: bool
    is_active: bool
    category: str
    tags: list
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

class CloneTemplateRequest(BaseModel):
    name: str | None = None
    artist_id: uuid.UUID | None = None
    release_id: uuid.UUID | None = None


# ── Onboarding ───────────────────────────────────────────────────

class OnboardingStepResponse(BaseModel):
    key: str
    label: str
    description: str
    is_completed: bool
    is_required: bool

class OnboardingStatusResponse(BaseModel):
    completed: bool
    steps: list[OnboardingStepResponse]
    progress_pct: int


class CampaignDestinationReport(BaseModel):
    campaign_id: uuid.UUID
    canonical_url: str | None
    total_posts: int
    posts_with_destination: int
    posts_missing_destination: list[PostDestinationWarning]

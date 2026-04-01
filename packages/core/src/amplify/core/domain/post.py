"""Post domain models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class PostStatus(str, Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PENDING_APPROVAL = "pending_approval"
    PUBLISHED = "published"
    FAILED = "failed"


# Valid state transitions for the post state machine.
VALID_TRANSITIONS: dict[PostStatus, set[PostStatus]] = {
    PostStatus.DRAFT: {PostStatus.QUEUED, PostStatus.SCHEDULED, PostStatus.PUBLISHING},
    PostStatus.QUEUED: {PostStatus.APPROVED, PostStatus.DRAFT, PostStatus.SCHEDULED, PostStatus.PUBLISHING},
    PostStatus.APPROVED: {PostStatus.SCHEDULED, PostStatus.PUBLISHING, PostStatus.DRAFT},
    PostStatus.SCHEDULED: {PostStatus.PUBLISHING, PostStatus.DRAFT},
    PostStatus.PUBLISHING: {PostStatus.PUBLISHED, PostStatus.PENDING_APPROVAL, PostStatus.FAILED, PostStatus.SCHEDULED, PostStatus.DRAFT},
    PostStatus.PENDING_APPROVAL: {PostStatus.PUBLISHED, PostStatus.FAILED},
    PostStatus.PUBLISHED: {PostStatus.SCHEDULED},
    PostStatus.FAILED: {PostStatus.QUEUED, PostStatus.DRAFT, PostStatus.SCHEDULED, PostStatus.PUBLISHED},
}


class IntegrationMode(str, Enum):
    """Whether a platform integration is fully automatic or human-assisted."""

    AUTOMATIC = "automatic"
    ASSISTED = "assisted"


class Platform(str, Enum):
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    YOUTUBE = "youtube"
    BANDCAMP = "bandcamp"
    LINKTREE = "linktree"
    EMAIL = "email"
    OTHER = "other"


# Which platforms publish automatically vs require manual steps.
PLATFORM_INTEGRATION_MODE: dict[Platform, IntegrationMode] = {
    Platform.INSTAGRAM: IntegrationMode.AUTOMATIC,
    Platform.TIKTOK: IntegrationMode.AUTOMATIC,
    Platform.YOUTUBE: IntegrationMode.AUTOMATIC,
    Platform.BANDCAMP: IntegrationMode.ASSISTED,
    Platform.LINKTREE: IntegrationMode.ASSISTED,
    Platform.EMAIL: IntegrationMode.AUTOMATIC,
    Platform.OTHER: IntegrationMode.ASSISTED,
}


PLATFORM_METADATA: dict[Platform, dict] = {
    Platform.INSTAGRAM: {
        "label": "Instagram",
        "mode": "automatic",
        "description": "Auto-publish photos, reels, and stories via Graph API",
        "capabilities": ["publish", "comments", "metrics", "stories"],
    },
    Platform.TIKTOK: {
        "label": "TikTok",
        "mode": "automatic",
        "description": "Auto-publish videos and fetch analytics via Content API",
        "capabilities": ["publish", "comments", "metrics", "drafts"],
    },
    Platform.YOUTUBE: {
        "label": "YouTube",
        "mode": "automatic",
        "description": "Auto-upload videos with metadata via Data API v3",
        "capabilities": ["publish", "comments", "metrics"],
    },
    Platform.BANDCAMP: {
        "label": "Bandcamp",
        "mode": "assisted",
        "description": "Manual workflow with checklists, reminders, and URL validation",
        "capabilities": ["url_validation", "reminders", "checklists", "metadata"],
    },
    Platform.LINKTREE: {
        "label": "Linktree",
        "mode": "assisted",
        "description": "Manual sync with tracked link generation and verification",
        "capabilities": ["url_validation", "link_tracking", "verification"],
    },
    Platform.EMAIL: {
        "label": "Email",
        "mode": "automatic",
        "description": "Send email campaigns via configured provider",
        "capabilities": ["publish", "metrics"],
    },
    Platform.OTHER: {
        "label": "Other",
        "mode": "assisted",
        "description": "Custom platform with manual workflow",
        "capabilities": [],
    },
}


class Post(BaseModel):
    """A social media or marketing post."""

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    campaign_id: UUID | None = None
    channel_id: UUID
    platform: Platform
    status: PostStatus = PostStatus.DRAFT
    content_text: str = ""
    media_urls: list[str] = Field(default_factory=list)
    scheduled_at: datetime | None = None
    published_at: datetime | None = None
    platform_post_id: str | None = None
    permalink: str | None = None
    destination_url: str | None = None
    engagement: dict = Field(default_factory=dict)
    retry_count: int = 0
    max_retries: int = 3
    last_error: str | None = None
    policy_decision: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def can_transition_to(self, target: PostStatus) -> bool:
        """Check if a transition from current status to target is valid."""
        return target in VALID_TRANSITIONS.get(self.status, set())


class PostCreate(BaseModel):
    """Schema for creating a new post."""

    campaign_id: UUID | None = None
    channel_id: UUID
    platform: Platform
    content_text: str = ""
    media_urls: list[str] = Field(default_factory=list)
    scheduled_at: datetime | None = None


class PostUpdate(BaseModel):
    """Schema for updating an existing post."""

    content_text: str | None = None
    media_urls: list[str] | None = None
    status: PostStatus | None = None
    scheduled_at: datetime | None = None
    platform_post_id: str | None = None
    permalink: str | None = None
    destination_url: str | None = None
    engagement: dict | None = None
    last_error: str | None = None

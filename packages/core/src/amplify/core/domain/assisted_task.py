"""Domain models for assisted-integration task workflows.

Platforms like Bandcamp and Linktree don't have full publish APIs,
so we model their workflows as human-assisted tasks — checklists,
reminders, URL validation, and verification checks.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    WAITING_VERIFICATION = "waiting_verification"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class ChecklistItem(BaseModel):
    """A single step in an assisted workflow checklist."""

    key: str
    label: str
    is_completed: bool = False
    completed_at: datetime | None = None
    notes: str = ""


class URLValidation(BaseModel):
    """Result of validating a platform URL."""

    url: str
    is_valid: bool = False
    status_code: int | None = None
    title: str | None = None
    error: str | None = None
    checked_at: datetime = Field(default_factory=datetime.utcnow)


class AssistedTask(BaseModel):
    """A manual task in an assisted-integration workflow."""

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    channel_id: UUID | None = None
    campaign_id: UUID | None = None
    release_id: UUID | None = None
    platform: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM
    checklist: list[ChecklistItem] = Field(default_factory=list)
    url_validations: list[URLValidation] = Field(default_factory=list)
    due_at: datetime | None = None
    reminder_at: datetime | None = None
    reminder_sent: bool = False
    completed_at: datetime | None = None
    assigned_to: UUID | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def checklist_progress(self) -> tuple[int, int]:
        """Return (completed, total) checklist items."""
        total = len(self.checklist)
        done = sum(1 for c in self.checklist if c.is_completed)
        return done, total

    @property
    def is_checklist_complete(self) -> bool:
        return all(c.is_completed for c in self.checklist) if self.checklist else True


class AssistedTaskCreate(BaseModel):
    channel_id: UUID | None = None
    campaign_id: UUID | None = None
    release_id: UUID | None = None
    platform: str
    title: str
    description: str = ""
    priority: TaskPriority = TaskPriority.MEDIUM
    checklist: list[ChecklistItem] = Field(default_factory=list)
    due_at: datetime | None = None
    reminder_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)


class AssistedTaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_at: datetime | None = None
    reminder_at: datetime | None = None
    assigned_to: UUID | None = None
    metadata: dict | None = None


# ── Bandcamp workflow templates ─────────────────────────────────


def bandcamp_release_checklist() -> list[ChecklistItem]:
    """Standard checklist for publishing a release on Bandcamp."""
    return [
        ChecklistItem(key="upload_audio", label="Upload audio files to Bandcamp"),
        ChecklistItem(key="set_metadata", label="Set title, artist, and track listing"),
        ChecklistItem(key="upload_artwork", label="Upload cover artwork (1400x1400 min)"),
        ChecklistItem(key="set_pricing", label="Set pricing (free / name-your-price / fixed)"),
        ChecklistItem(key="write_description", label="Write release description and credits"),
        ChecklistItem(key="set_tags", label="Add genre tags (up to 10)"),
        ChecklistItem(key="set_release_date", label="Set release date"),
        ChecklistItem(key="preview_page", label="Preview the release page"),
        ChecklistItem(key="publish", label="Click Publish on Bandcamp"),
        ChecklistItem(key="verify_url", label="Verify the live URL is accessible"),
    ]


def bandcamp_update_checklist() -> list[ChecklistItem]:
    """Checklist for updating an existing Bandcamp release."""
    return [
        ChecklistItem(key="update_description", label="Update description text"),
        ChecklistItem(key="update_pricing", label="Review and update pricing if needed"),
        ChecklistItem(key="verify_changes", label="Verify changes are live"),
    ]


# ── Linktree workflow templates ─────────────────────────────────


def linktree_sync_checklist() -> list[ChecklistItem]:
    """Standard checklist for syncing links on Linktree."""
    return [
        ChecklistItem(key="review_links", label="Review current Linktree link list"),
        ChecklistItem(key="add_new_links", label="Add new release / campaign links"),
        ChecklistItem(key="update_order", label="Reorder links by priority"),
        ChecklistItem(key="remove_stale", label="Remove or archive stale links"),
        ChecklistItem(key="verify_tracking", label="Verify UTM parameters on tracked links"),
        ChecklistItem(key="test_links", label="Test all links open correctly"),
        ChecklistItem(key="screenshot", label="Take screenshot for records (optional)"),
    ]


def linktree_new_release_checklist() -> list[ChecklistItem]:
    """Checklist for adding a new release to Linktree."""
    return [
        ChecklistItem(key="generate_link", label="Generate tracked link with UTM params"),
        ChecklistItem(key="add_to_linktree", label="Add link to Linktree profile"),
        ChecklistItem(key="set_position", label="Position link at top of list"),
        ChecklistItem(key="add_thumbnail", label="Add release artwork as link thumbnail"),
        ChecklistItem(key="verify_live", label="Verify link is live and clickable"),
        ChecklistItem(key="verify_tracking", label="Confirm click tracking is working"),
    ]

"""Prompt version registry — in-memory representations of versioned prompts.

These dataclasses are the canonical schema for prompt versioning.  They
mirror the DB models in packages/db but carry no ORM dependency, so the
learning package and agent runtime can use them without importing SQLAlchemy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class PromptStatus(str, Enum):
    """Lifecycle status of a prompt version."""

    DRAFT = "draft"           # being authored, not assignable
    ACTIVE = "active"         # available for assignment
    SHADOW = "shadow"         # runs in parallel but doesn't affect live output
    DEPRECATED = "deprecated" # no longer assignable, existing assignments warn
    ARCHIVED = "archived"     # hard-removed from rotation


@dataclass(frozen=True)
class PromptVersion:
    """Immutable snapshot of a versioned prompt template."""

    id: str                        # UUID as string
    prompt_key: str                # e.g. "planner_system", "content_caption"
    version: int                   # monotonically increasing per prompt_key
    template: str                  # the prompt text
    system_prompt: str | None = None
    model_id: str | None = None    # e.g. "claude-sonnet-4-20250514"
    parameters: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    author: str | None = None
    status: str = "active"         # PromptStatus value
    created_at: str = ""           # ISO datetime

    @property
    def is_active(self) -> bool:
        return self.status == PromptStatus.ACTIVE

    @property
    def is_shadow(self) -> bool:
        return self.status == PromptStatus.SHADOW

    @property
    def is_assignable(self) -> bool:
        return self.status in (PromptStatus.ACTIVE, PromptStatus.SHADOW)

    def to_metadata(self) -> dict[str, Any]:
        """Compact metadata for embedding in lineage records."""
        return {
            "prompt_version_id": self.id,
            "prompt_key": self.prompt_key,
            "version": self.version,
            "model_id": self.model_id,
            "status": self.status,
        }


@dataclass
class PromptAssignment:
    """Maps a prompt version to a tenant for a specific prompt key."""

    id: str
    tenant_id: str
    prompt_key: str
    prompt_version_id: str
    reason: str = ""
    assigned_by: str | None = None  # user_id
    is_active: bool = True
    created_at: str = ""

    @property
    def is_shadow(self) -> bool:
        return "shadow" in self.reason.lower()


@dataclass
class PromptRegistry:
    """In-memory registry of prompt versions and assignments.

    Populated by the service layer before agent execution, or manually
    in tests.  The resolver uses this to determine which version to use.
    """

    versions: dict[str, list[PromptVersion]] = field(default_factory=dict)
    assignments: dict[str, dict[str, PromptAssignment]] = field(default_factory=dict)

    def add_version(self, version: PromptVersion) -> None:
        """Register a prompt version."""
        self.versions.setdefault(version.prompt_key, []).append(version)
        # Keep sorted by version number descending
        self.versions[version.prompt_key].sort(
            key=lambda v: v.version, reverse=True
        )

    def add_assignment(self, assignment: PromptAssignment) -> None:
        """Register a tenant assignment."""
        self.assignments.setdefault(assignment.tenant_id, {})[
            assignment.prompt_key
        ] = assignment

    def get_latest_active(self, prompt_key: str) -> PromptVersion | None:
        """Get the highest-version active prompt for a key."""
        for v in self.versions.get(prompt_key, []):
            if v.is_active:
                return v
        return None

    def get_version_by_id(self, version_id: str) -> PromptVersion | None:
        """Look up a specific version by ID."""
        for versions in self.versions.values():
            for v in versions:
                if v.id == version_id:
                    return v
        return None

    def get_assignment(
        self, tenant_id: str, prompt_key: str,
    ) -> PromptAssignment | None:
        """Get the active assignment for a tenant and prompt key."""
        assignment = self.assignments.get(tenant_id, {}).get(prompt_key)
        if assignment and assignment.is_active:
            return assignment
        return None

    def get_shadow_version(self, prompt_key: str) -> PromptVersion | None:
        """Get the shadow version for a prompt key (if any)."""
        for v in self.versions.get(prompt_key, []):
            if v.is_shadow:
                return v
        return None

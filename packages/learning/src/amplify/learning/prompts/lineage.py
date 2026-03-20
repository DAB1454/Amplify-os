"""Generation lineage tracking — links every output to its prompt version.

Every agent output (post draft, asset, plan, reply) gets a lineage
attachment that records exactly which prompt version produced it.  This
enables:
- Attributing learning outcomes to specific prompt versions
- A/B testing prompt changes by comparing outcome distributions
- Debugging prompt regressions by filtering by version
- Shadow mode evaluation by running two versions and comparing outputs
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from amplify.learning.prompts.resolver import ResolvedPrompt


@dataclass
class PromptLineageAttachment:
    """Metadata attached to a generated artifact to record its prompt lineage.

    Stored as JSON in the `prompt_lineage` column on posts, assets, and
    learning events.
    """

    # What produced this output
    prompt_version_id: str
    prompt_key: str
    prompt_version_number: int
    prompt_status: str              # active, shadow, deprecated
    resolution_path: str            # tenant_assignment, latest_active, fallback

    # Agent execution context
    agent_name: str
    run_id: str                     # agent run UUID
    model_id: str | None = None     # LLM model used

    # Assignment context (if tenant-specific)
    assignment_id: str | None = None
    assignment_reason: str | None = None

    # Shadow evaluation
    shadow_version_id: str | None = None
    shadow_version_number: int | None = None
    is_shadow_output: bool = False  # True if this output came from the shadow prompt

    # Timestamps
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON column storage."""
        return {
            "prompt_version_id": self.prompt_version_id,
            "prompt_key": self.prompt_key,
            "prompt_version_number": self.prompt_version_number,
            "prompt_status": self.prompt_status,
            "resolution_path": self.resolution_path,
            "agent_name": self.agent_name,
            "run_id": self.run_id,
            "model_id": self.model_id,
            "assignment_id": self.assignment_id,
            "assignment_reason": self.assignment_reason,
            "shadow_version_id": self.shadow_version_id,
            "shadow_version_number": self.shadow_version_number,
            "is_shadow_output": self.is_shadow_output,
            "generated_at": self.generated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PromptLineageAttachment:
        """Reconstruct from JSON column data."""
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class ShadowComparison:
    """Side-by-side comparison of live and shadow prompt outputs."""

    live_lineage: PromptLineageAttachment
    shadow_lineage: PromptLineageAttachment
    live_output_preview: str = ""    # first 500 chars of live output
    shadow_output_preview: str = ""  # first 500 chars of shadow output
    comparison_notes: str = ""       # human or automated diff notes

    def to_dict(self) -> dict[str, Any]:
        return {
            "live": self.live_lineage.to_dict(),
            "shadow": self.shadow_lineage.to_dict(),
            "live_output_preview": self.live_output_preview,
            "shadow_output_preview": self.shadow_output_preview,
            "comparison_notes": self.comparison_notes,
        }


class GenerationLineage:
    """Factory for creating lineage attachments from resolved prompts.

    Usage::

        lineage = GenerationLineage(agent_name="planner", run_id=str(uuid4()))

        # After prompt resolution
        attachment = lineage.create_attachment(resolved_prompt)

        # Attach to output
        post.prompt_lineage = attachment.to_dict()

        # For shadow mode
        shadow_attachment = lineage.create_shadow_attachment(
            resolved_prompt, shadow_output=True
        )
    """

    def __init__(self, agent_name: str, run_id: str, model_id: str | None = None) -> None:
        self._agent_name = agent_name
        self._run_id = run_id
        self._model_id = model_id

    def create_attachment(
        self,
        resolved: ResolvedPrompt,
        is_shadow_output: bool = False,
    ) -> PromptLineageAttachment:
        """Create a lineage attachment from a resolved prompt."""
        version = resolved.version
        attachment = PromptLineageAttachment(
            prompt_version_id=version.id,
            prompt_key=version.prompt_key,
            prompt_version_number=version.version,
            prompt_status=version.status,
            resolution_path=resolved.resolution_path,
            agent_name=self._agent_name,
            run_id=self._run_id,
            model_id=self._model_id or version.model_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
            is_shadow_output=is_shadow_output,
        )

        if resolved.assignment:
            attachment.assignment_id = resolved.assignment.id
            attachment.assignment_reason = resolved.assignment.reason

        if resolved.shadow_version:
            attachment.shadow_version_id = resolved.shadow_version.id
            attachment.shadow_version_number = resolved.shadow_version.version

        return attachment

    def create_shadow_attachment(
        self, resolved: ResolvedPrompt,
    ) -> PromptLineageAttachment | None:
        """Create a lineage attachment for the shadow version, if present."""
        if not resolved.shadow_version:
            return None

        shadow_v = resolved.shadow_version
        return PromptLineageAttachment(
            prompt_version_id=shadow_v.id,
            prompt_key=shadow_v.prompt_key,
            prompt_version_number=shadow_v.version,
            prompt_status=shadow_v.status,
            resolution_path="shadow",
            agent_name=self._agent_name,
            run_id=self._run_id,
            model_id=self._model_id or shadow_v.model_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
            is_shadow_output=True,
        )

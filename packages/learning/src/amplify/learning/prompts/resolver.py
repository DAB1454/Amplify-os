"""Prompt version resolver — determines which prompt version to use.

Resolution order:
1. Tenant-specific assignment (if active)
2. Latest active version for the prompt key (global default)
3. Fallback template (hardcoded prompt from code)

Also resolves shadow versions that run in parallel without affecting
live output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from amplify.learning.prompts.registry import (
    PromptAssignment,
    PromptRegistry,
    PromptStatus,
    PromptVersion,
)


@dataclass(frozen=True)
class ResolvedPrompt:
    """The result of prompt resolution — what the agent should use."""

    version: PromptVersion          # the resolved version
    template: str                   # the actual prompt text to use
    resolution_path: str            # how it was resolved
    assignment: PromptAssignment | None = None  # tenant assignment, if used
    shadow_version: PromptVersion | None = None  # shadow version to evaluate

    @property
    def is_fallback(self) -> bool:
        return self.resolution_path == "fallback"

    def to_metadata(self) -> dict[str, Any]:
        """Metadata for lineage attachment."""
        meta = {
            **self.version.to_metadata(),
            "resolution_path": self.resolution_path,
        }
        if self.assignment:
            meta["assignment_id"] = self.assignment.id
            meta["assignment_reason"] = self.assignment.reason
        if self.shadow_version:
            meta["shadow_version_id"] = self.shadow_version.id
            meta["shadow_version"] = self.shadow_version.version
        return meta


class PromptResolver:
    """Resolves the correct prompt version for a given context.

    Usage::

        registry = PromptRegistry()
        # ... populate with versions and assignments ...
        resolver = PromptResolver(registry)

        resolved = resolver.resolve(
            prompt_key="planner_system",
            tenant_id="abc-123",
            fallback="You are a campaign planner...",
        )
        # resolved.template is the prompt to use
        # resolved.version has full metadata
        # resolved.shadow_version is set if a shadow test is running
    """

    def __init__(self, registry: PromptRegistry) -> None:
        self._registry = registry

    def resolve(
        self,
        prompt_key: str,
        tenant_id: str | None = None,
        fallback: str = "",
    ) -> ResolvedPrompt:
        """Resolve the prompt version to use.

        Resolution order:
        1. Tenant-specific active assignment
        2. Latest active version for prompt_key
        3. Fallback template
        """
        assignment: PromptAssignment | None = None
        version: PromptVersion | None = None

        # Step 1: Check tenant assignment
        if tenant_id:
            assignment = self._registry.get_assignment(tenant_id, prompt_key)
            if assignment:
                version = self._registry.get_version_by_id(
                    assignment.prompt_version_id,
                )
                # Only use if the assigned version is still usable
                if version and version.status in (
                    PromptStatus.ACTIVE,
                    PromptStatus.SHADOW,
                    PromptStatus.DEPRECATED,
                ):
                    path = "tenant_assignment"
                else:
                    version = None
                    assignment = None

        # Step 2: Fall back to latest active
        if version is None:
            version = self._registry.get_latest_active(prompt_key)
            if version:
                path = "latest_active"

        # Step 3: Fall back to hardcoded
        if version is None:
            version = PromptVersion(
                id="fallback",
                prompt_key=prompt_key,
                version=0,
                template=fallback,
                status="active",
            )
            path = "fallback"

        # Resolve shadow version (if any)
        shadow = self._registry.get_shadow_version(prompt_key)
        if shadow and shadow.id == version.id:
            shadow = None  # don't shadow yourself

        template = version.system_prompt or version.template

        return ResolvedPrompt(
            version=version,
            template=template,
            resolution_path=path,
            assignment=assignment,
            shadow_version=shadow,
        )

    def resolve_all_for_agent(
        self,
        agent_name: str,
        tenant_id: str | None = None,
        prompt_keys: list[str] | None = None,
        fallbacks: dict[str, str] | None = None,
    ) -> dict[str, ResolvedPrompt]:
        """Resolve all prompt versions for an agent.

        Convenience method that resolves multiple prompt keys at once.
        Typically an agent has a system prompt and one or more task templates.
        """
        fallbacks = fallbacks or {}
        keys = prompt_keys or [f"{agent_name}_system"]
        return {
            key: self.resolve(key, tenant_id, fallbacks.get(key, ""))
            for key in keys
        }

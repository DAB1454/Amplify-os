"""Tests for prompt versioning core — registry, resolver, and lineage.

Covers:
- PromptRegistry: version and assignment management
- PromptResolver: resolution order (assignment → latest active → fallback)
- GenerationLineage: attachment creation, shadow mode, serialization
- Edge cases: empty registry, deprecated versions, ties
"""

from __future__ import annotations

import uuid

import pytest

from amplify.learning.prompts.registry import (
    PromptAssignment,
    PromptRegistry,
    PromptStatus,
    PromptVersion,
)
from amplify.learning.prompts.resolver import PromptResolver, ResolvedPrompt
from amplify.learning.prompts.lineage import (
    GenerationLineage,
    PromptLineageAttachment,
    ShadowComparison,
)


# ── Fixtures ─────────────────────────────────────────────────────


def _ver(
    key: str = "planner_system",
    version: int = 1,
    template: str = "You are a planner.",
    status: str = "active",
    vid: str | None = None,
    model_id: str | None = None,
) -> PromptVersion:
    return PromptVersion(
        id=vid or str(uuid.uuid4()),
        prompt_key=key,
        version=version,
        template=template,
        status=status,
        model_id=model_id,
    )


def _assign(
    tenant_id: str,
    key: str,
    version_id: str,
    reason: str = "",
) -> PromptAssignment:
    return PromptAssignment(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        prompt_key=key,
        prompt_version_id=version_id,
        reason=reason,
    )


TENANT_A = "aaaa-1111"
TENANT_B = "bbbb-2222"


# ═══════════════════════════════════════════════════════════════════
# PromptVersion model
# ═══════════════════════════════════════════════════════════════════


class TestPromptVersion:
    def test_active_version(self):
        v = _ver(status="active")
        assert v.is_active
        assert v.is_assignable
        assert not v.is_shadow

    def test_shadow_version(self):
        v = _ver(status="shadow")
        assert not v.is_active
        assert v.is_shadow
        assert v.is_assignable

    def test_deprecated_not_assignable(self):
        v = _ver(status="deprecated")
        assert not v.is_assignable

    def test_to_metadata(self):
        v = _ver(vid="abc-123", version=3, model_id="claude-sonnet-4-20250514")
        meta = v.to_metadata()
        assert meta["prompt_version_id"] == "abc-123"
        assert meta["version"] == 3
        assert meta["model_id"] == "claude-sonnet-4-20250514"


# ═══════════════════════════════════════════════════════════════════
# PromptRegistry
# ═══════════════════════════════════════════════════════════════════


class TestPromptRegistry:
    def test_add_and_get_latest_active(self):
        reg = PromptRegistry()
        v1 = _ver(version=1, template="v1")
        v2 = _ver(version=2, template="v2")
        reg.add_version(v1)
        reg.add_version(v2)
        latest = reg.get_latest_active("planner_system")
        assert latest is not None
        assert latest.version == 2

    def test_latest_active_skips_shadow(self):
        reg = PromptRegistry()
        v1 = _ver(version=1, status="active")
        v2 = _ver(version=2, status="shadow")
        reg.add_version(v1)
        reg.add_version(v2)
        latest = reg.get_latest_active("planner_system")
        assert latest is not None
        assert latest.version == 1

    def test_get_version_by_id(self):
        reg = PromptRegistry()
        vid = str(uuid.uuid4())
        v = _ver(vid=vid)
        reg.add_version(v)
        found = reg.get_version_by_id(vid)
        assert found is not None
        assert found.id == vid

    def test_get_version_by_id_not_found(self):
        reg = PromptRegistry()
        assert reg.get_version_by_id("nonexistent") is None

    def test_assignment_lookup(self):
        reg = PromptRegistry()
        vid = str(uuid.uuid4())
        a = _assign(TENANT_A, "planner_system", vid)
        reg.add_assignment(a)
        found = reg.get_assignment(TENANT_A, "planner_system")
        assert found is not None
        assert found.prompt_version_id == vid

    def test_assignment_not_found_different_tenant(self):
        reg = PromptRegistry()
        a = _assign(TENANT_A, "planner_system", "v1")
        reg.add_assignment(a)
        assert reg.get_assignment(TENANT_B, "planner_system") is None

    def test_inactive_assignment_not_returned(self):
        reg = PromptRegistry()
        a = _assign(TENANT_A, "planner_system", "v1")
        a.is_active = False
        reg.add_assignment(a)
        assert reg.get_assignment(TENANT_A, "planner_system") is None

    def test_get_shadow_version(self):
        reg = PromptRegistry()
        v1 = _ver(version=1, status="active")
        v2 = _ver(version=2, status="shadow")
        reg.add_version(v1)
        reg.add_version(v2)
        shadow = reg.get_shadow_version("planner_system")
        assert shadow is not None
        assert shadow.version == 2

    def test_no_shadow_version(self):
        reg = PromptRegistry()
        v1 = _ver(version=1, status="active")
        reg.add_version(v1)
        assert reg.get_shadow_version("planner_system") is None

    def test_empty_registry(self):
        reg = PromptRegistry()
        assert reg.get_latest_active("anything") is None
        assert reg.get_shadow_version("anything") is None
        assert reg.get_assignment("t1", "k1") is None


# ═══════════════════════════════════════════════════════════════════
# PromptResolver — assignment logic
# ═══════════════════════════════════════════════════════════════════


class TestResolverAssignment:
    def test_tenant_assignment_takes_priority(self):
        reg = PromptRegistry()
        v1 = _ver(version=1, vid="v1-id", template="global v1")
        v2 = _ver(version=2, vid="v2-id", template="tenant-specific v2")
        reg.add_version(v1)
        reg.add_version(v2)
        reg.add_assignment(_assign(TENANT_A, "planner_system", "v2-id"))

        resolver = PromptResolver(reg)
        resolved = resolver.resolve("planner_system", TENANT_A)
        assert resolved.resolution_path == "tenant_assignment"
        assert resolved.version.id == "v2-id"

    def test_falls_through_to_latest_active(self):
        reg = PromptRegistry()
        v1 = _ver(version=1, template="v1")
        v2 = _ver(version=2, template="v2")
        reg.add_version(v1)
        reg.add_version(v2)

        resolver = PromptResolver(reg)
        resolved = resolver.resolve("planner_system", TENANT_A)
        assert resolved.resolution_path == "latest_active"
        assert resolved.version.version == 2

    def test_falls_through_to_fallback(self):
        reg = PromptRegistry()
        resolver = PromptResolver(reg)
        resolved = resolver.resolve(
            "planner_system",
            TENANT_A,
            fallback="You are a fallback planner.",
        )
        assert resolved.resolution_path == "fallback"
        assert resolved.is_fallback
        assert resolved.template == "You are a fallback planner."

    def test_assignment_to_deprecated_still_works(self):
        """Existing assignments to deprecated versions still resolve."""
        reg = PromptRegistry()
        vid = str(uuid.uuid4())
        v = _ver(vid=vid, version=1, status="deprecated")
        reg.add_version(v)
        reg.add_assignment(_assign(TENANT_A, "planner_system", vid))

        resolver = PromptResolver(reg)
        resolved = resolver.resolve("planner_system", TENANT_A)
        assert resolved.resolution_path == "tenant_assignment"

    def test_assignment_to_archived_skipped(self):
        """Assignments pointing to archived versions fall through."""
        reg = PromptRegistry()
        vid = str(uuid.uuid4())
        v = _ver(vid=vid, version=1, status="archived")
        reg.add_version(v)
        reg.add_assignment(_assign(TENANT_A, "planner_system", vid))

        resolver = PromptResolver(reg)
        resolved = resolver.resolve(
            "planner_system", TENANT_A, fallback="fb",
        )
        # Should fall through to fallback (no other active versions)
        assert resolved.resolution_path == "fallback"

    def test_no_tenant_id_skips_assignment(self):
        reg = PromptRegistry()
        v = _ver(version=1, vid="v1")
        reg.add_version(v)
        reg.add_assignment(_assign(TENANT_A, "planner_system", "v1"))

        resolver = PromptResolver(reg)
        resolved = resolver.resolve("planner_system", tenant_id=None)
        assert resolved.resolution_path == "latest_active"


class TestResolverShadow:
    def test_shadow_version_attached(self):
        reg = PromptRegistry()
        v1 = _ver(version=1, vid="v1", status="active")
        v2 = _ver(version=2, vid="v2", status="shadow")
        reg.add_version(v1)
        reg.add_version(v2)

        resolver = PromptResolver(reg)
        resolved = resolver.resolve("planner_system", TENANT_A)
        assert resolved.shadow_version is not None
        assert resolved.shadow_version.version == 2

    def test_no_shadow_when_only_active(self):
        reg = PromptRegistry()
        v1 = _ver(version=1, status="active")
        reg.add_version(v1)

        resolver = PromptResolver(reg)
        resolved = resolver.resolve("planner_system", TENANT_A)
        assert resolved.shadow_version is None

    def test_shadow_not_self(self):
        """If the resolved version IS the shadow, shadow_version is None."""
        reg = PromptRegistry()
        vid = str(uuid.uuid4())
        v = _ver(version=1, vid=vid, status="shadow")
        reg.add_version(v)
        reg.add_assignment(_assign(TENANT_A, "planner_system", vid))

        resolver = PromptResolver(reg)
        resolved = resolver.resolve("planner_system", TENANT_A)
        assert resolved.shadow_version is None


class TestResolverBulk:
    def test_resolve_all_for_agent(self):
        reg = PromptRegistry()
        reg.add_version(_ver(key="planner_system", version=1))
        reg.add_version(_ver(key="planner_plan_campaign", version=1,
                             template="Plan a campaign..."))

        resolver = PromptResolver(reg)
        resolved = resolver.resolve_all_for_agent(
            "planner",
            TENANT_A,
            prompt_keys=["planner_system", "planner_plan_campaign"],
        )
        assert "planner_system" in resolved
        assert "planner_plan_campaign" in resolved

    def test_resolve_all_defaults_to_system(self):
        reg = PromptRegistry()
        reg.add_version(_ver(key="planner_system", version=1))

        resolver = PromptResolver(reg)
        resolved = resolver.resolve_all_for_agent("planner", TENANT_A)
        assert "planner_system" in resolved


class TestResolvedPromptMetadata:
    def test_to_metadata_includes_resolution_path(self):
        reg = PromptRegistry()
        v = _ver(version=1, vid="v1-id")
        reg.add_version(v)

        resolver = PromptResolver(reg)
        resolved = resolver.resolve("planner_system", TENANT_A)
        meta = resolved.to_metadata()
        assert meta["resolution_path"] == "latest_active"
        assert meta["prompt_version_id"] == "v1-id"

    def test_to_metadata_includes_assignment(self):
        reg = PromptRegistry()
        vid = str(uuid.uuid4())
        v = _ver(vid=vid, version=1)
        reg.add_version(v)
        reg.add_assignment(_assign(TENANT_A, "planner_system", vid, reason="A/B test"))

        resolver = PromptResolver(reg)
        resolved = resolver.resolve("planner_system", TENANT_A)
        meta = resolved.to_metadata()
        assert meta["assignment_reason"] == "A/B test"

    def test_to_metadata_includes_shadow(self):
        reg = PromptRegistry()
        v1 = _ver(version=1, vid="v1", status="active")
        v2 = _ver(version=2, vid="v2", status="shadow")
        reg.add_version(v1)
        reg.add_version(v2)

        resolver = PromptResolver(reg)
        resolved = resolver.resolve("planner_system", TENANT_A)
        meta = resolved.to_metadata()
        assert meta["shadow_version_id"] == "v2"


# ═══════════════════════════════════════════════════════════════════
# GenerationLineage
# ═══════════════════════════════════════════════════════════════════


class TestGenerationLineage:
    def _resolve(self, with_shadow: bool = False) -> ResolvedPrompt:
        reg = PromptRegistry()
        v1 = _ver(version=1, vid="v1", status="active", model_id="claude-sonnet-4-20250514")
        reg.add_version(v1)
        if with_shadow:
            v2 = _ver(version=2, vid="v2", status="shadow")
            reg.add_version(v2)
        reg.add_assignment(_assign(TENANT_A, "planner_system", "v1", reason="test"))

        resolver = PromptResolver(reg)
        return resolver.resolve("planner_system", TENANT_A)

    def test_create_attachment(self):
        resolved = self._resolve()
        lineage = GenerationLineage("planner", run_id="run-123")
        att = lineage.create_attachment(resolved)

        assert att.prompt_version_id == "v1"
        assert att.prompt_key == "planner_system"
        assert att.prompt_version_number == 1
        assert att.agent_name == "planner"
        assert att.run_id == "run-123"
        assert att.resolution_path == "tenant_assignment"
        assert att.assignment_reason == "test"
        assert not att.is_shadow_output
        assert att.generated_at  # non-empty timestamp

    def test_create_attachment_model_from_version(self):
        resolved = self._resolve()
        lineage = GenerationLineage("planner", run_id="run-123")
        att = lineage.create_attachment(resolved)
        assert att.model_id == "claude-sonnet-4-20250514"

    def test_create_attachment_model_override(self):
        resolved = self._resolve()
        lineage = GenerationLineage("planner", run_id="run-123", model_id="custom-model")
        att = lineage.create_attachment(resolved)
        assert att.model_id == "custom-model"

    def test_create_shadow_attachment(self):
        resolved = self._resolve(with_shadow=True)
        lineage = GenerationLineage("planner", run_id="run-123")
        shadow_att = lineage.create_shadow_attachment(resolved)
        assert shadow_att is not None
        assert shadow_att.prompt_version_id == "v2"
        assert shadow_att.is_shadow_output
        assert shadow_att.resolution_path == "shadow"

    def test_no_shadow_attachment_when_no_shadow(self):
        resolved = self._resolve(with_shadow=False)
        lineage = GenerationLineage("planner", run_id="run-123")
        assert lineage.create_shadow_attachment(resolved) is None


class TestLineageSerialization:
    def test_to_dict_roundtrip(self):
        att = PromptLineageAttachment(
            prompt_version_id="v1",
            prompt_key="planner_system",
            prompt_version_number=3,
            prompt_status="active",
            resolution_path="tenant_assignment",
            agent_name="planner",
            run_id="run-456",
            model_id="claude-sonnet-4-20250514",
            assignment_id="a1",
            assignment_reason="A/B test",
            shadow_version_id="v2",
            shadow_version_number=4,
            generated_at="2026-03-20T12:00:00Z",
        )
        d = att.to_dict()
        restored = PromptLineageAttachment.from_dict(d)
        assert restored.prompt_version_id == "v1"
        assert restored.prompt_version_number == 3
        assert restored.agent_name == "planner"
        assert restored.shadow_version_id == "v2"
        assert restored.generated_at == "2026-03-20T12:00:00Z"

    def test_to_dict_minimal(self):
        att = PromptLineageAttachment(
            prompt_version_id="v1",
            prompt_key="content_caption",
            prompt_version_number=1,
            prompt_status="active",
            resolution_path="fallback",
            agent_name="content",
            run_id="run-789",
        )
        d = att.to_dict()
        assert d["prompt_version_id"] == "v1"
        assert d["is_shadow_output"] is False
        assert d["shadow_version_id"] is None


class TestShadowComparison:
    def test_shadow_comparison_serialization(self):
        live = PromptLineageAttachment(
            prompt_version_id="v1", prompt_key="k", prompt_version_number=1,
            prompt_status="active", resolution_path="latest_active",
            agent_name="planner", run_id="r1",
        )
        shadow = PromptLineageAttachment(
            prompt_version_id="v2", prompt_key="k", prompt_version_number=2,
            prompt_status="shadow", resolution_path="shadow",
            agent_name="planner", run_id="r1", is_shadow_output=True,
        )
        comp = ShadowComparison(
            live_lineage=live,
            shadow_lineage=shadow,
            live_output_preview="Plan A...",
            shadow_output_preview="Plan B...",
        )
        d = comp.to_dict()
        assert d["live"]["prompt_version_id"] == "v1"
        assert d["shadow"]["prompt_version_id"] == "v2"
        assert d["live_output_preview"] == "Plan A..."

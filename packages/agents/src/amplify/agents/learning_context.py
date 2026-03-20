"""Learning context — structured tenant pattern data for agent consumption.

Provides a schema-based bridge between the learning subsystem and agents.
Each LearningContext is a snapshot of what the tenant has learned, formatted
for injection into agent system/user prompts.

Every recommendation carries an ID so that downstream outputs can reference
which recommendations they applied — enabling full audit traceability.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LearnedPreference(BaseModel):
    """A single learned preference or anti-preference for prompt injection."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    feature: str  # e.g. "content_hook_family"
    value: str  # e.g. "question"
    direction: str = "winning"  # "winning" or "losing"
    confidence: float = 0.0
    title: str = ""
    explanation: str = ""
    is_pinned: bool = False
    evidence_summary: str = ""  # one-liner for compact prompt injection


class LearningContext(BaseModel):
    """Snapshot of tenant learning state for a single agent run.

    Frozen at invocation time — the agent sees a consistent view of
    the learning state regardless of concurrent updates.
    """

    snapshot_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str = ""
    captured_at: str = ""  # ISO datetime

    # Preferences the agent should follow
    recommendations: list[LearnedPreference] = Field(default_factory=list)

    # Anti-patterns the agent should avoid
    avoid: list[LearnedPreference] = Field(default_factory=list)

    # Data status
    has_enough_data: bool = False
    total_observations: int = 0
    patterns_version: int = 0  # incremented on each nightly run

    def is_empty(self) -> bool:
        """True if no recommendations or avoid patterns exist."""
        return not self.recommendations and not self.avoid

    def recommendation_ids(self) -> list[str]:
        """Return IDs of all recommendations in this context."""
        return [r.id for r in self.recommendations]

    def avoid_ids(self) -> list[str]:
        """Return IDs of all avoid patterns in this context."""
        return [a.id for a in self.avoid]

    def format_for_prompt(self) -> str:
        """Render a structured text block for injection into an agent prompt.

        If no data exists, returns a brief note that the system has no
        learned preferences yet.
        """
        if self.is_empty():
            return (
                "## Tenant Learning\n"
                "No learned preferences are available yet. "
                "Use general best practices. "
                "As this tenant publishes more content and outcomes are measured, "
                "the system will learn what works best for their audience.\n"
            )

        lines = ["## Tenant Learning (data-driven preferences)\n"]
        lines.append(
            f"Based on {self.total_observations} posts with measured outcomes.\n"
        )

        if self.recommendations:
            lines.append("### Recommended approaches")
            for r in self.recommendations:
                pin_tag = " [PINNED by admin]" if r.is_pinned else ""
                lines.append(
                    f"- [{r.id}] **{r.title}** (confidence: {r.confidence:.0%}){pin_tag}"
                )
                if r.evidence_summary:
                    lines.append(f"  Evidence: {r.evidence_summary}")
            lines.append("")

        if self.avoid:
            lines.append("### Patterns to avoid")
            for a in self.avoid:
                lines.append(
                    f"- [{a.id}] **{a.title}** (confidence: {a.confidence:.0%})"
                )
                if a.evidence_summary:
                    lines.append(f"  Evidence: {a.evidence_summary}")
            lines.append("")

        lines.append(
            "Use these preferences to guide decisions. "
            "Reference recommendation IDs (e.g. [abc123]) in your output "
            "when a decision was influenced by a learned preference.\n"
        )

        return "\n".join(lines)

    def to_audit_snapshot(self) -> dict[str, Any]:
        """Return a compact dict suitable for persisting as an audit record."""
        return {
            "snapshot_id": self.snapshot_id,
            "tenant_id": self.tenant_id,
            "captured_at": self.captured_at,
            "recommendation_ids": self.recommendation_ids(),
            "avoid_ids": self.avoid_ids(),
            "has_enough_data": self.has_enough_data,
            "total_observations": self.total_observations,
            "patterns_version": self.patterns_version,
        }


class AgentDecisionRecord(BaseModel):
    """Tracks which recommendations an agent actually used in its output.

    Attached to AgentResult for audit persistence.
    """

    learning_snapshot_id: str = ""
    recommendations_available: list[str] = Field(default_factory=list)
    recommendations_applied: list[str] = Field(default_factory=list)
    recommendations_ignored: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


def build_learning_context(
    tenant_id: str,
    patterns: list[dict],
    recommendations: list[dict],
    data_status: dict,
) -> LearningContext:
    """Build a LearningContext from service-layer data.

    Parameters
    ----------
    tenant_id : str
        The tenant UUID as string.
    patterns : list[dict]
        Active TenantPattern records (from get_patterns).
    recommendations : list[dict]
        Recommendation dicts (from get_recommendations).
    data_status : dict
        Data status dict (from get_data_status).
    """
    recs = []
    for r in recommendations:
        evidence = r.get("evidence", {})
        lift = evidence.get("lift_vs_baseline", 0)
        count = evidence.get("raw_count", 0)
        evidence_summary = f"{abs(lift):.0f}% lift over baseline from {count} posts"

        recs.append(LearnedPreference(
            feature=r.get("feature", ""),
            value=r.get("recommended_value", ""),
            direction="winning",
            confidence=r.get("confidence", 0),
            title=r.get("title", ""),
            explanation=r.get("explanation", ""),
            is_pinned=evidence.get("pinned", False),
            evidence_summary=evidence_summary,
        ))

    avoid = []
    for p in patterns:
        if p.get("direction") == "losing" and p.get("is_active", True):
            evidence = p.get("evidence") or {}
            lift = evidence.get("lift_vs_baseline", 0)
            count = evidence.get("raw_count", 0)
            avoid.append(LearnedPreference(
                feature=p.get("subject_feature", ""),
                value=p.get("subject_value", ""),
                direction="losing",
                confidence=p.get("confidence", 0),
                title=p.get("title", ""),
                explanation=p.get("explanation", ""),
                evidence_summary=f"{abs(lift):.0f}% below baseline from {count} posts",
            ))

    return LearningContext(
        tenant_id=tenant_id,
        captured_at=datetime.utcnow().isoformat(),
        recommendations=recs,
        avoid=avoid,
        has_enough_data=data_status.get("has_enough_data", False),
        total_observations=data_status.get("feature_vectors", 0),
    )

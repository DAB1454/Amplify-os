"""AutomationAudit ORM model.

Every action the autonomy worker takes (or decides not to take) writes a
row here. The user reads this log to verify what the agent did while they
were away. Without this table you cannot safely turn on autonomous mode.

Distinct from AuditLogModel, which records human-initiated changes for
compliance/security purposes. AutomationAuditModel is specifically the
"agent decision trail" — what the worker chose, why, and what happened.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Float, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from amplify.db.base import Base, TenantMixin, TimestampMixin


# Closed set of agent action verbs. Anything outside this set is a bug —
# the worker decider must always pick one of these.
AUTOMATION_ACTIONS = (
    "tick",            # worker woke up but did nothing (logged anyway for liveness)
    "plan",            # generated a new campaign plan
    "approve_batch",   # auto-approved one or more pending posts
    "generate_media",  # generated media for a post
    "publish",         # published a scheduled post
    "start_experiment",  # paired an untested asset with a proven baseline
    "score_experiment",  # scored a finished experiment
    "rerank",          # recomputed prior_metrics + reordered queue
    "escalate",        # confidence too low — handed back to manual
    "pause",           # auto-paused due to a guardrail trip
    "resume",          # auto-resumed (rare; usually user action)
    "noop",            # decider chose to do nothing this tick
    "error",           # exception during action execution
)


# Outcome categories — narrow set so the UI can render badges + filter.
AUTOMATION_OUTCOMES = ("success", "skipped", "escalated", "failed")


class AutomationAuditModel(Base, TimestampMixin, TenantMixin):
    __tablename__ = "automation_audits"
    __table_args__ = (
        Index(
            "ix_automation_audits_tenant_created",
            "tenant_id", "created_at",
        ),
        Index(
            "ix_automation_audits_tenant_action",
            "tenant_id", "action",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )

    # The verb (one of AUTOMATION_ACTIONS).
    action: Mapped[str] = mapped_column(String(40), nullable=False)

    # Free-text reason — what the decider was looking at when it picked this action.
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Forecast / decider confidence (0..1). Nullable for actions that don't have one.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Outcome of the action attempt.
    outcome: Mapped[str] = mapped_column(String(20), nullable=False, default="success")

    # Optional error string if outcome=failed.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Link back to the affected entity, if any.
    entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    # Decider state snapshot — JSON dump of the inputs the decider saw.
    # Useful for debugging "why did it pick that?" after the fact.
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)

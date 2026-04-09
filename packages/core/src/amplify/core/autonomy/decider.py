"""autonomy_decider — pure decision function for the autonomous worker.

Given the current state of one tenant, return a single ``Decision`` that
the worker tick will execute. Pure on purpose: no side effects, no
network calls, no writes — just SELECTs against the session and a
verdict. This makes the decider trivially testable and lets us dry-run
it from the UI ("show me what the agent would do right now") without
touching real state.

The worker (Task #18) is the *only* thing that calls execution code.
The decider only inspects + decides. If you find yourself reaching for
``session.add`` in this file, stop — push that to the worker instead.

Decision verbs map 1:1 to ``AUTOMATION_ACTIONS`` in
``amplify.db.models.automation_audit`` so every decision can be logged
verbatim.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.approval import ApprovalModel
from amplify.db.models.campaign import CampaignModel
from amplify.db.models.post import PostModel
from amplify.db.models.tenant import TenantModel


# Confidence floor below which an auto-approve decision is downgraded to
# escalate. The bandit (Task #21) will eventually feed real per-post
# scores into this; for now we apply it at the batch level using a
# placeholder value of 1.0 (= "we have no model yet, defer to the level").
DEFAULT_AUTO_APPROVE_CONFIDENCE = 0.65


# Levels in increasing autonomy. Order matters — used for >= comparisons.
LEVEL_ORDER = {
    "manual": 0,
    "assisted": 1,
    "auto_campaigns": 2,
    "autonomous": 3,
}


@dataclass
class Decision:
    """One decider verdict.

    ``action`` is always one of ``AUTOMATION_ACTIONS``. ``payload`` is a
    free-form dict the worker uses to actually execute the action (e.g.
    ``{"post_ids": [...]}`` for publish, ``{"campaign_id": ...}`` for
    plan). ``snapshot`` captures what the decider was looking at, and is
    written verbatim to the audit log so we can debug "why did it pick
    that?" months later.
    """

    action: str
    reason: str
    confidence: float | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    snapshot: dict[str, Any] = field(default_factory=dict)
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None


def _level_at_least(level: str, threshold: str) -> bool:
    return LEVEL_ORDER.get(level, 0) >= LEVEL_ORDER.get(threshold, 99)


async def decide_for_tenant(
    session: AsyncSession,
    tenant_id: uuid.UUID,
) -> Decision:
    """Inspect tenant state and return one Decision.

    Order of priority (first match wins):
      1. paused / manual         → noop
      2. due-to-publish posts    → publish
      3. pending approvals at assisted+ → approve_batch (or escalate)
      4. no active campaign at auto_campaigns+ → plan
      5. otherwise                → tick (heartbeat)
    """
    now = datetime.now(timezone.utc)

    # ── 1. Tenant gate ─────────────────────────────────────────
    tenant = (
        await session.execute(select(TenantModel).where(TenantModel.id == tenant_id))
    ).scalar_one_or_none()

    if tenant is None:
        # Tenant vanished mid-tick — bail out without an error row.
        return Decision(
            action="noop",
            reason="tenant not found",
            snapshot={"tenant_id": str(tenant_id)},
        )

    level = tenant.automation_level or "manual"
    paused = bool(tenant.automation_paused)

    base_snapshot: dict[str, Any] = {
        "tenant_id": str(tenant_id),
        "automation_level": level,
        "automation_paused": paused,
        "now": now.isoformat(),
    }

    if paused:
        return Decision(
            action="noop",
            reason="automation paused (kill switch)",
            snapshot=base_snapshot,
        )

    if level == "manual":
        return Decision(
            action="noop",
            reason="automation_level=manual; user is driving",
            snapshot=base_snapshot,
        )

    # ── 2. Anything due to publish? ────────────────────────────
    due_stmt = (
        select(PostModel.id)
        .where(
            PostModel.tenant_id == tenant_id,
            PostModel.status == "scheduled",
            PostModel.scheduled_at != None,  # noqa: E711
            PostModel.scheduled_at <= now,
        )
        .order_by(PostModel.scheduled_at.asc())
        .limit(20)
    )
    due_ids = list((await session.execute(due_stmt)).scalars().all())
    base_snapshot["due_to_publish"] = len(due_ids)

    if due_ids:
        return Decision(
            action="publish",
            reason=f"{len(due_ids)} post(s) past their scheduled time",
            confidence=1.0,
            payload={"post_ids": [str(pid) for pid in due_ids]},
            snapshot=base_snapshot,
            entity_type="post",
            entity_id=due_ids[0],
        )

    # ── 3. Pending approvals at assisted+ ──────────────────────
    if _level_at_least(level, "assisted"):
        pending_count = (
            await session.execute(
                select(func.count(ApprovalModel.id)).where(
                    ApprovalModel.tenant_id == tenant_id,
                    ApprovalModel.status == "pending",
                )
            )
        ).scalar_one()
        base_snapshot["pending_approvals"] = int(pending_count or 0)

        if pending_count and _level_at_least(level, "auto_campaigns"):
            # Until the bandit is wired (Task #21), use a flat placeholder
            # confidence so the action is logged but the worker can still
            # gate on the threshold.
            confidence = DEFAULT_AUTO_APPROVE_CONFIDENCE
            if confidence >= DEFAULT_AUTO_APPROVE_CONFIDENCE:
                return Decision(
                    action="approve_batch",
                    reason=f"auto-approving {pending_count} pending post(s)",
                    confidence=confidence,
                    payload={"limit": int(pending_count)},
                    snapshot=base_snapshot,
                )
            return Decision(
                action="escalate",
                reason="confidence below auto-approve threshold",
                confidence=confidence,
                snapshot=base_snapshot,
            )

        if pending_count:
            # assisted level: AI generates but never auto-approves.
            return Decision(
                action="noop",
                reason=(
                    f"{pending_count} pending approval(s) waiting on user "
                    f"(level=assisted)"
                ),
                snapshot=base_snapshot,
            )

    # ── 4. No active campaign at auto_campaigns+ ───────────────
    if _level_at_least(level, "auto_campaigns"):
        active_campaign = (
            await session.execute(
                select(CampaignModel.id)
                .where(
                    CampaignModel.tenant_id == tenant_id,
                    CampaignModel.status.in_(("active", "running", "draft")),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        base_snapshot["has_active_campaign"] = active_campaign is not None

        if active_campaign is None:
            return Decision(
                action="plan",
                reason="no active campaign — generating new plan",
                confidence=0.7,
                payload={},
                snapshot=base_snapshot,
            )

    # ── 5. Heartbeat ───────────────────────────────────────────
    return Decision(
        action="tick",
        reason="nothing to do this tick",
        snapshot=base_snapshot,
    )

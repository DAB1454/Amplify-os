"""automation_tick — periodic autonomy worker.

Runs once per minute. For every tenant whose automation_level is not
"manual" and that isn't paused, it asks ``autonomy_decider`` what to do
and either executes the decision (for low-risk actions) or records it
to the audit log (for actions that need follow-up work).

This job is the *only* thing that calls ``decide_for_tenant``. The
decider stays a pure function so it can be unit-tested in isolation;
the side-effecting glue lives here.

Decision verb → execution mapping:
  noop / tick      → log only
  publish          → log only (scan_scheduled job already publishes due
                     posts every 60s; we don't double-enqueue)
  approve_batch    → flip up to N pending approvals to "approved"
  plan / generate_media / start_experiment / score_experiment / rerank
                   → recorded with outcome=skipped + reason explaining
                     that the executor isn't wired yet (Tasks #20–#22).
                     The audit row is enough for the user to see the
                     agent *wanted* to act.
  escalate / pause / resume / error → log only

Each tenant runs in its own DB session so a failure on one tenant
doesn't poison the whole tick.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

logger = logging.getLogger(__name__)


# Cap how many approvals one tick can auto-approve. Prevents a runaway
# auto-approval if a backlog of 200 pending posts exists — we want the
# user to see the audit trail and have a chance to pause first.
MAX_APPROVALS_PER_TICK = 10


async def automation_tick(payload: dict | None = None) -> dict:
    """One pass of the autonomy loop across all eligible tenants."""
    # Imports inside the function: keeps the worker import-time graph
    # small and matches the style of other jobs in this directory.
    from amplify.db.session import get_async_session
    from amplify.db.models.tenant import TenantModel
    from app.config import Settings

    # Decider + audit service live in the shared core package so both
    # the API and the worker can import them without crossing app
    # boundaries.
    from amplify.core.autonomy import AutomationAuditService, decide_for_tenant
    from amplify.core.services.notification_service import NotificationService

    settings = Settings()
    logger.info("automation_tick: starting pass")

    eligible_tenant_ids: list[str] = []
    decisions_made: list[dict[str, Any]] = []

    # Pass 1: pull eligible tenant ids in one short-lived session so we
    # don't hold a connection while running per-tenant work.
    async with get_async_session(settings.database_url) as db:
        stmt = select(TenantModel.id, TenantModel.automation_level).where(
            TenantModel.automation_paused.is_(False),
            TenantModel.automation_level != "manual",
            TenantModel.is_active.is_(True),
        )
        rows = (await db.execute(stmt)).all()
        eligible_tenant_ids = [str(r[0]) for r in rows]

    if not eligible_tenant_ids:
        logger.info("automation_tick: no eligible tenants")
        return {"status": "ok", "tenants_checked": 0, "decisions": []}

    # Pass 2: per-tenant decider + execute. New session per tenant so
    # one bad row doesn't roll back everything.
    for tid_str in eligible_tenant_ids:
        try:
            async with get_async_session(settings.database_url) as db:
                import uuid as _uuid
                tid = _uuid.UUID(tid_str)
                decision = await decide_for_tenant(db, tid)
                outcome, error = await _execute_decision(db, tid, decision)

                audit = AutomationAuditService(db, tid)
                await audit.record(
                    action=decision.action,
                    reason=decision.reason,
                    confidence=decision.confidence,
                    outcome=outcome,
                    error=error,
                    entity_type=decision.entity_type,
                    entity_id=decision.entity_id,
                    snapshot=decision.snapshot,
                )

                # Emit in-app notifications for high-signal events.
                # Low-value ticks (noop/tick/publish) stay silent so the
                # bell doesn't become noise the user learns to ignore.
                try:
                    await _maybe_notify(db, tid, decision, outcome, error)
                except Exception:
                    logger.exception(
                        "automation_tick: notify failed for %s", tid_str
                    )

                decisions_made.append(
                    {
                        "tenant_id": tid_str,
                        "action": decision.action,
                        "outcome": outcome,
                    }
                )
        except Exception:
            logger.exception("automation_tick: tenant %s failed", tid_str)
            # Best-effort: log a synthetic error row in a fresh session
            # so the user sees that *something* went wrong this tick.
            try:
                async with get_async_session(settings.database_url) as db2:
                    import uuid as _uuid
                    audit = AutomationAuditService(db2, _uuid.UUID(tid_str))
                    await audit.record(
                        action="error",
                        reason="automation_tick crashed",
                        outcome="failed",
                        error="see worker logs",
                    )
            except Exception:
                logger.exception("automation_tick: failed to record error row")

    logger.info(
        "automation_tick: finished — %d tenants, %d decisions",
        len(eligible_tenant_ids),
        len(decisions_made),
    )
    return {
        "status": "ok",
        "tenants_checked": len(eligible_tenant_ids),
        "decisions": decisions_made,
    }


async def _maybe_notify(db, tenant_id, decision, outcome: str, error: str | None) -> None:
    """Create bell-feed notifications for decisions the user should see.

    Silent cases (intentional): noop/tick/publish success, skipped plan
    (nothing to plan), skipped approve_batch (no pending approvals).
    """
    from amplify.core.services.notification_service import NotificationService

    action = decision.action
    service = NotificationService(db, tenant_id)

    # Failures always notify owners/admins — something needs attention.
    if outcome == "failed":
        await service.notify_tenant(
            event_type=f"automation.{action}.failed",
            title=f"Automation failed: {action}",
            body=error or decision.reason or "See automation log for details.",
            severity="error",
            url="/automation",
            roles=("owner", "admin"),
        )
        return

    if outcome != "success":
        return

    if action == "plan":
        await service.notify_tenant(
            event_type="automation.plan.succeeded",
            title="New plan drafted",
            body=decision.reason or "The agent generated a new campaign plan.",
            severity="success",
            url="/calendar",
        )
    elif action == "approve_batch":
        count = 0
        if isinstance(decision.snapshot, dict):
            count = int(decision.snapshot.get("approved_count") or 0)
        if not count and decision.payload:
            count = int(decision.payload.get("limit") or 0)
        await service.notify_tenant(
            event_type="automation.approve_batch",
            title=(
                f"Auto-approved {count} post{'s' if count != 1 else ''}"
                if count
                else "Auto-approved pending posts"
            ),
            body=decision.reason or "",
            severity="info",
            url="/approvals",
        )
    elif action == "escalate":
        await service.notify_tenant(
            event_type="automation.escalate",
            title="Agent escalated to you",
            body=decision.reason or "The agent wants a human to weigh in.",
            severity="warning",
            url="/approvals",
            roles=("owner", "admin"),
        )


async def _execute_decision(db, tenant_id, decision) -> tuple[str, str | None]:
    """Run the side-effects implied by a Decision.

    Returns ``(outcome, error)`` where outcome is one of the
    AUTOMATION_OUTCOMES values. ``error`` is set only when outcome is
    ``failed``.
    """
    from amplify.db.models.approval import ApprovalModel
    from amplify.core.domain.approval import ApprovalStatus
    from datetime import datetime

    action = decision.action

    if action in ("noop", "tick", "publish", "escalate", "pause", "resume", "error"):
        # Read-only or already handled by another scheduled job.
        return "success", None

    if action == "approve_batch":
        limit = min(int(decision.payload.get("limit", 0) or 0), MAX_APPROVALS_PER_TICK)
        if limit <= 0:
            return "skipped", None

        # Pull more candidates than the limit so the confidence gate
        # has room to reject some without starving the tick.
        over_fetch = max(limit * 2, limit + 5)
        stmt = (
            select(ApprovalModel)
            .where(
                ApprovalModel.tenant_id == tenant_id,
                ApprovalModel.status == "pending",
                ApprovalModel.post_id.isnot(None),
            )
            .order_by(ApprovalModel.created_at.asc())
            .limit(over_fetch)
        )
        approvals = list((await db.execute(stmt)).scalars().all())
        if not approvals:
            return "skipped", None

        # Confidence gate — score each post and drop any below threshold.
        # The threshold is env-configurable via AMPLIFY_AUTO_APPROVE_MIN_SCORE.
        from amplify.db.models.post import PostModel
        from amplify.core.autonomy import default_min_score, score_post
        from sqlalchemy.orm import selectinload

        threshold = default_min_score()
        post_ids = [ap.post_id for ap in approvals if ap.post_id]
        posts_by_id: dict = {}
        if post_ids:
            post_rows = (
                await db.execute(
                    select(PostModel)
                    .options(selectinload(PostModel.campaign))
                    .where(PostModel.id.in_(post_ids))
                )
            ).scalars().all()
            posts_by_id = {p.id: p for p in post_rows}

        approved: list = []
        filtered: list[dict] = []
        for ap in approvals:
            if len(approved) >= limit:
                break
            post = posts_by_id.get(ap.post_id) if ap.post_id else None
            if post is None:
                # No post to score — be conservative and skip.
                filtered.append(
                    {"approval_id": str(ap.id), "reason": "post_missing"}
                )
                continue
            result = await score_post(db, tenant_id, post)
            if result.score < threshold:
                filtered.append(
                    {
                        "approval_id": str(ap.id),
                        "post_id": str(post.id),
                        "score": round(result.score, 4),
                        "factors": result.top_factors,
                    }
                )
                continue
            approved.append((ap, result))

        if not approved:
            # Every candidate failed the gate. Record what we saw in the
            # snapshot so the user can see *why* the agent did nothing,
            # then bail out. Outcome is "skipped" not "failed" — a
            # blocked approval isn't a bug, it's the gate working.
            if isinstance(decision.snapshot, dict):
                decision.snapshot["confidence_gate"] = {
                    "threshold": threshold,
                    "filtered": filtered[:20],
                    "approved_count": 0,
                }
            return "skipped", None

        now = datetime.utcnow()
        for ap, _ in approved:
            ap.status = ApprovalStatus.APPROVED.value
            ap.reviewed_at = now
            # reviewed_by stays NULL — that's how we mark "agent did it"
            # vs a human reviewer. The UI can show "auto-approved" when
            # reviewed_by is null but status is approved.
        await db.flush()

        # Fold the gate result into the snapshot so the audit row
        # shows how many passed, how many were filtered, and the score
        # distribution. Bounded to 20 filtered entries to keep rows small.
        if isinstance(decision.snapshot, dict):
            decision.snapshot["confidence_gate"] = {
                "threshold": threshold,
                "approved_count": len(approved),
                "filtered_count": len(filtered),
                "filtered": filtered[:20],
                "approved_scores": [
                    {
                        "approval_id": str(ap.id),
                        "score": round(r.score, 4),
                        "profile": r.profile,
                    }
                    for ap, r in approved[:20]
                ],
            }
        return "success", None

    if action == "plan":
        # Pick the campaign the decider was about (payload may carry a
        # specific campaign_id; otherwise use the oldest active campaign
        # for this tenant, or bail out if there isn't one).
        from amplify.db.models.campaign import CampaignModel
        from amplify.core.planner import plan_campaign

        target_id = decision.payload.get("campaign_id")
        if target_id:
            try:
                import uuid as _uuid
                target_uuid = _uuid.UUID(str(target_id))
            except ValueError:
                return "failed", f"invalid campaign_id in payload: {target_id!r}"
        else:
            target_uuid = (
                await db.execute(
                    select(CampaignModel.id)
                    .where(
                        CampaignModel.tenant_id == tenant_id,
                        CampaignModel.status.in_(("active", "draft")),
                    )
                    .order_by(CampaignModel.created_at.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()

        if target_uuid is None:
            return "skipped", None

        try:
            await plan_campaign(
                db,
                tenant_id=tenant_id,
                campaign_id=target_uuid,
                user_id=None,  # agent is the actor
            )
        except Exception as exc:
            return "failed", f"{type(exc).__name__}: {exc}"
        return "success", None

    if action == "generate_content":
        # Generate AI captions for draft posts in the target campaign.
        from app.jobs.generate_content import generate_content as _gen_content

        target_id = decision.payload.get("campaign_id") if decision.payload else None
        if not target_id:
            # Fall back to oldest active campaign with drafts
            from amplify.db.models.campaign import CampaignModel
            target_id = (
                await db.execute(
                    select(CampaignModel.id)
                    .where(
                        CampaignModel.tenant_id == tenant_id,
                        CampaignModel.status.in_(("active", "draft")),
                    )
                    .order_by(CampaignModel.created_at.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if target_id is None:
                return "skipped", None
            target_id = str(target_id)

        try:
            result = await _gen_content({
                "campaign_id": str(target_id),
                "tenant_id": str(tenant_id),
            })
            if result.get("pieces_generated", 0) == 0:
                return "skipped", None
            if isinstance(decision.snapshot, dict):
                decision.snapshot["content_result"] = result
            return "success", None
        except Exception as exc:
            return "failed", f"{type(exc).__name__}: {exc}"

    if action in ("generate_media", "start_experiment", "score_experiment", "rerank"):
        # Executors not wired yet — log the intent so the user sees
        # what the agent wanted to do (Tasks #20–#22).
        return "skipped", None

    # Should never happen — decider is the gatekeeper.
    return "failed", f"unhandled action {action!r}"

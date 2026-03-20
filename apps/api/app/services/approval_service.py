"""Approval service — queue, review, comment, and notify."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from amplify.core.domain.approval import (
    ApprovalRulesEngine,
    ApprovalStatus,
    RiskLevel,
    create_default_approval_engine,
)
from amplify.core.notifications.dispatcher import (
    NotificationDispatcher,
    NotificationPayload,
    Severity,
    create_default_dispatcher,
)
from amplify.db.models.approval import ApprovalCommentModel, ApprovalModel
from amplify.db.models.audit_log import AuditLogModel

logger = logging.getLogger(__name__)

APPROVAL_EXPIRY_HOURS = 72


class ApprovalService:
    """Manages the full approval lifecycle."""

    def __init__(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        *,
        rules_engine: ApprovalRulesEngine | None = None,
        notifier: NotificationDispatcher | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.rules_engine = rules_engine or create_default_approval_engine()
        self.notifier = notifier or create_default_dispatcher()

    # ── check whether approval is needed ──────────────────────────

    def check_action(self, action_type: str, payload: dict) -> dict:
        """Evaluate whether an action needs approval.

        Returns {"requires_approval": bool, "risk_level": str, "reasons": [...]}.
        """
        result = self.rules_engine.evaluate(action_type, payload)
        return {
            "requires_approval": result.requires_approval,
            "risk_level": result.risk_level.value,
            "reasons": result.reasons,
        }

    # ── create ────────────────────────────────────────────────────

    async def create_approval(
        self,
        action_type: str,
        entity_type: str = "",
        entity_id: uuid.UUID | None = None,
        requested_by: uuid.UUID | None = None,
        notes: str = "",
        action_payload: dict | None = None,
    ) -> ApprovalModel:
        """Create a new approval request, assess risk, and notify."""
        payload = action_payload or {}
        risk = self.rules_engine.evaluate(action_type, payload)

        approval = ApprovalModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            action_type=action_type,
            entity_type=entity_type,
            entity_id=entity_id,
            risk_level=risk.risk_level.value,
            risk_reasons=risk.reasons,
            policy_snapshot=payload.get("policy_snapshot", {}),
            action_payload=payload,
            post_id=entity_id if entity_type == "post" else None,
            asset_id=entity_id if entity_type == "asset" else None,
            requested_by=requested_by,
            status=ApprovalStatus.PENDING.value,
            notes=notes,
            expires_at=datetime.utcnow() + timedelta(hours=APPROVAL_EXPIRY_HOURS),
        )
        self.db.add(approval)
        await self.db.flush()
        approval_id = approval.id

        # Audit
        await self._audit(
            "approval.created",
            approval_id,
            requested_by,
            {"action_type": action_type, "risk_level": risk.risk_level.value},
        )

        # Notify
        severity = {
            RiskLevel.LOW: Severity.INFO,
            RiskLevel.MEDIUM: Severity.INFO,
            RiskLevel.HIGH: Severity.WARNING,
            RiskLevel.CRITICAL: Severity.CRITICAL,
        }.get(risk.risk_level, Severity.INFO)

        await self.notifier.dispatch(NotificationPayload(
            event_type="approval.created",
            severity=severity,
            title=f"New approval request: {action_type}",
            body=f"Risk: {risk.risk_level.value}. {'; '.join(risk.reasons)}" if risk.reasons else f"Risk: {risk.risk_level.value}",
            tenant_id=str(self.tenant_id),
            entity_type="approval",
            entity_id=str(approval_id),
            actor_id=str(requested_by) if requested_by else "",
        ))

        # Re-fetch with selectinload so all columns (including server-
        # generated updated_at) and relationships are loaded for serialization.
        return await self._get(approval_id)

    # ── decisions ─────────────────────────────────────────────────

    async def approve(
        self,
        approval_id: uuid.UUID,
        reviewer_id: uuid.UUID | None = None,
        comment: str = "",
    ) -> ApprovalModel:
        approval = await self._get(approval_id)
        self._assert_actionable(approval)

        approval.status = ApprovalStatus.APPROVED.value
        approval.reviewed_by = reviewer_id
        approval.reviewed_at = datetime.utcnow()

        if comment:
            await self._add_comment(approval_id, reviewer_id, comment)

        await self.db.flush()

        await self._audit("approval.approved", approval_id, reviewer_id, {"comment": comment})
        await self.notifier.dispatch(NotificationPayload(
            event_type="approval.approved",
            severity=Severity.INFO,
            title=f"Approval granted: {approval.action_type}",
            body=comment or "Approved without comment.",
            tenant_id=str(self.tenant_id),
            entity_type="approval",
            entity_id=str(approval_id),
            actor_id=str(reviewer_id) if reviewer_id else "",
        ))

        return await self._get(approval_id)

    async def reject(
        self,
        approval_id: uuid.UUID,
        reviewer_id: uuid.UUID | None = None,
        comment: str = "",
    ) -> ApprovalModel:
        approval = await self._get(approval_id)
        self._assert_actionable(approval)

        approval.status = ApprovalStatus.REJECTED.value
        approval.reviewed_by = reviewer_id
        approval.reviewed_at = datetime.utcnow()

        if comment:
            await self._add_comment(approval_id, reviewer_id, comment)

        await self.db.flush()

        await self._audit("approval.rejected", approval_id, reviewer_id, {"comment": comment})
        await self.notifier.dispatch(NotificationPayload(
            event_type="approval.rejected",
            severity=Severity.WARNING,
            title=f"Approval rejected: {approval.action_type}",
            body=comment or "Rejected without comment.",
            tenant_id=str(self.tenant_id),
            entity_type="approval",
            entity_id=str(approval_id),
            actor_id=str(reviewer_id) if reviewer_id else "",
        ))

        return await self._get(approval_id)

    async def request_revision(
        self,
        approval_id: uuid.UUID,
        reviewer_id: uuid.UUID | None = None,
        comment: str = "",
    ) -> ApprovalModel:
        approval = await self._get(approval_id)
        self._assert_actionable(approval)

        approval.status = ApprovalStatus.REVISION_REQUESTED.value
        approval.reviewed_by = reviewer_id
        approval.reviewed_at = datetime.utcnow()

        if comment:
            await self._add_comment(approval_id, reviewer_id, comment)

        await self.db.flush()

        await self._audit("approval.revision_requested", approval_id, reviewer_id, {"comment": comment})
        await self.notifier.dispatch(NotificationPayload(
            event_type="approval.revision_requested",
            severity=Severity.INFO,
            title=f"Revision requested: {approval.action_type}",
            body=comment or "Changes requested.",
            tenant_id=str(self.tenant_id),
            entity_type="approval",
            entity_id=str(approval_id),
            actor_id=str(reviewer_id) if reviewer_id else "",
        ))

        return await self._get(approval_id)

    async def resubmit(
        self,
        approval_id: uuid.UUID,
        requester_id: uuid.UUID | None = None,
        notes: str = "",
        updated_payload: dict | None = None,
    ) -> ApprovalModel:
        approval = await self._get(approval_id)
        if approval.status not in (
            ApprovalStatus.REVISION_REQUESTED.value,
            ApprovalStatus.REJECTED.value,
        ):
            raise ValueError(
                f"Cannot resubmit approval in {approval.status!r} state"
            )

        approval.status = ApprovalStatus.RESUBMITTED.value
        approval.reviewed_by = None
        approval.reviewed_at = None
        if notes:
            approval.notes = notes
        if updated_payload is not None:
            approval.action_payload = updated_payload

        # Re-assess risk with updated payload
        risk = self.rules_engine.evaluate(
            approval.action_type, approval.action_payload
        )
        approval.risk_level = risk.risk_level.value
        approval.risk_reasons = risk.reasons

        # Reset expiry
        approval.expires_at = datetime.utcnow() + timedelta(hours=APPROVAL_EXPIRY_HOURS)

        await self.db.flush()

        await self._audit("approval.resubmitted", approval_id, requester_id, {"notes": notes})
        await self.notifier.dispatch(NotificationPayload(
            event_type="approval.resubmitted",
            severity=Severity.INFO,
            title=f"Approval resubmitted: {approval.action_type}",
            body=notes or "Resubmitted for review.",
            tenant_id=str(self.tenant_id),
            entity_type="approval",
            entity_id=str(approval_id),
            actor_id=str(requester_id) if requester_id else "",
        ))

        return await self._get(approval_id)

    # ── comments ──────────────────────────────────────────────────

    async def add_comment(
        self,
        approval_id: uuid.UUID,
        user_id: uuid.UUID | None,
        body: str,
    ) -> ApprovalCommentModel:
        await self._get(approval_id)  # validate exists
        return await self._add_comment(approval_id, user_id, body)

    async def list_comments(self, approval_id: uuid.UUID) -> list[ApprovalCommentModel]:
        stmt = (
            select(ApprovalCommentModel)
            .where(ApprovalCommentModel.approval_id == approval_id)
            .order_by(ApprovalCommentModel.created_at)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ── queries ───────────────────────────────────────────────────

    async def get(self, approval_id: uuid.UUID) -> ApprovalModel:
        return await self._get(approval_id)

    async def list_pending(
        self,
        offset: int = 0,
        limit: int = 50,
    ) -> list[ApprovalModel]:
        stmt = (
            select(ApprovalModel)
            .where(
                ApprovalModel.tenant_id == self.tenant_id,
                ApprovalModel.status.in_([
                    ApprovalStatus.PENDING.value,
                    ApprovalStatus.RESUBMITTED.value,
                ]),
            )
            .options(selectinload(ApprovalModel.comments))
            .order_by(ApprovalModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_history(
        self,
        offset: int = 0,
        limit: int = 50,
        status_filter: str | None = None,
    ) -> list[ApprovalModel]:
        stmt = (
            select(ApprovalModel)
            .where(ApprovalModel.tenant_id == self.tenant_id)
            .options(selectinload(ApprovalModel.comments))
            .order_by(ApprovalModel.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if status_filter:
            stmt = stmt.where(ApprovalModel.status == status_filter)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count_pending(self) -> int:
        stmt = (
            select(func.count())
            .select_from(ApprovalModel)
            .where(
                ApprovalModel.tenant_id == self.tenant_id,
                ApprovalModel.status.in_([
                    ApprovalStatus.PENDING.value,
                    ApprovalStatus.RESUBMITTED.value,
                ]),
            )
        )
        result = await self.db.execute(stmt)
        return result.scalar_one()

    async def get_audit_trail(
        self,
        approval_id: uuid.UUID,
        limit: int = 50,
    ) -> list[AuditLogModel]:
        stmt = (
            select(AuditLogModel)
            .where(
                AuditLogModel.tenant_id == self.tenant_id,
                AuditLogModel.entity_type == "approval",
                AuditLogModel.entity_id == approval_id,
            )
            .order_by(AuditLogModel.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ── internals ─────────────────────────────────────────────────

    async def _get(self, approval_id: uuid.UUID) -> ApprovalModel:
        # populate_existing=True forces SQLAlchemy to overwrite the identity-
        # mapped instance with fresh DB state, including re-executing the
        # selectinload.  Without it, a second _get() in the same session
        # (e.g. the re-fetch after approve/reject) returns the cached object
        # with stale relationships and expired server-default columns like
        # updated_at, which triggers MissingGreenlet on serialization.
        stmt = (
            select(ApprovalModel)
            .where(
                ApprovalModel.id == approval_id,
                ApprovalModel.tenant_id == self.tenant_id,
            )
            .options(selectinload(ApprovalModel.comments))
            .execution_options(populate_existing=True)
        )
        result = await self.db.execute(stmt)
        approval = result.scalar_one_or_none()
        if approval is None:
            raise ValueError(f"Approval {approval_id} not found")
        return approval

    @staticmethod
    def _assert_actionable(approval: ApprovalModel) -> None:
        actionable = {
            ApprovalStatus.PENDING.value,
            ApprovalStatus.RESUBMITTED.value,
        }
        if approval.status not in actionable:
            raise ValueError(
                f"Cannot act on approval in {approval.status!r} state"
            )
        if approval.expires_at and approval.expires_at < datetime.utcnow():
            raise ValueError("Approval has expired")

    async def _add_comment(
        self,
        approval_id: uuid.UUID,
        user_id: uuid.UUID | None,
        body: str,
    ) -> ApprovalCommentModel:
        comment = ApprovalCommentModel(
            id=uuid.uuid4(),
            approval_id=approval_id,
            user_id=user_id,
            body=body,
        )
        self.db.add(comment)
        await self.db.flush()
        # Refresh so server-generated columns (created_at, updated_at) are
        # loaded before Pydantic serialization — avoids MissingGreenlet.
        await self.db.refresh(comment)
        return comment

    async def _audit(
        self,
        action: str,
        approval_id: uuid.UUID,
        user_id: uuid.UUID | None,
        changes: dict | None = None,
    ) -> None:
        entry = AuditLogModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            user_id=user_id,
            action=action,
            entity_type="approval",
            entity_id=approval_id,
            changes=changes or {},
        )
        self.db.add(entry)
        await self.db.flush()

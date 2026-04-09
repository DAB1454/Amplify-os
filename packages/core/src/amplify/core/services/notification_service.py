"""NotificationService — writes rows to the in-app ``notifications`` table.

Shared between the API (so routes can notify a specific user) and the
worker (so ``automation_tick`` can surface what the agent did). Kept
deliberately thin: no fan-out to Slack/email — that's the job of the
existing ``amplify.core.notifications.dispatcher``. If you want both
in-app and Slack, call both.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.membership import MembershipModel
from amplify.db.models.notification import (
    NOTIFICATION_SEVERITIES,
    NotificationModel,
)


class NotificationService:
    """Persist in-app notifications for humans in a tenant."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID | str):
        self.session = session
        self.tenant_id = uuid.UUID(str(tenant_id))

    async def notify_user(
        self,
        user_id: uuid.UUID,
        *,
        event_type: str,
        title: str,
        body: str = "",
        severity: str = "info",
        url: str | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        meta: dict[str, Any] | None = None,
    ) -> NotificationModel:
        if severity not in NOTIFICATION_SEVERITIES:
            raise ValueError(
                f"unknown notification severity {severity!r}; "
                f"must be one of {NOTIFICATION_SEVERITIES}"
            )
        row = NotificationModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            user_id=user_id,
            event_type=event_type,
            severity=severity,
            title=title,
            body=body or "",
            url=url,
            entity_type=entity_type,
            entity_id=entity_id,
            meta=meta or {},
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def notify_tenant(
        self,
        *,
        event_type: str,
        title: str,
        body: str = "",
        severity: str = "info",
        url: str | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        meta: dict[str, Any] | None = None,
        roles: tuple[str, ...] | None = None,
    ) -> list[NotificationModel]:
        """Fan out one notification to every member of the tenant.

        Simpler than a broadcast row + last_read cursor, and per-user
        read state falls out for free. ``roles`` optionally restricts
        the fan-out (e.g. ``("owner", "admin")`` for escalations).
        """
        stmt = select(MembershipModel.user_id).where(
            MembershipModel.tenant_id == self.tenant_id
        )
        if roles:
            stmt = stmt.where(MembershipModel.role.in_(roles))

        user_ids = list((await self.session.execute(stmt)).scalars().all())
        rows: list[NotificationModel] = []
        for uid in user_ids:
            rows.append(
                await self.notify_user(
                    uid,
                    event_type=event_type,
                    title=title,
                    body=body,
                    severity=severity,
                    url=url,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    meta=meta,
                )
            )
        return rows

    async def mark_read(
        self,
        notification_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> NotificationModel | None:
        """Mark one notification as read. No-op if it doesn't belong to user."""
        row = (
            await self.session.execute(
                select(NotificationModel).where(
                    NotificationModel.id == notification_id,
                    NotificationModel.tenant_id == self.tenant_id,
                    NotificationModel.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.read_at is None:
            row.read_at = datetime.utcnow()
            await self.session.flush()
        return row

    async def mark_all_read(self, user_id: uuid.UUID) -> int:
        """Mark every unread notification for this user as read. Returns count."""
        stmt = select(NotificationModel).where(
            NotificationModel.tenant_id == self.tenant_id,
            NotificationModel.user_id == user_id,
            NotificationModel.read_at.is_(None),
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        now = datetime.utcnow()
        for r in rows:
            r.read_at = now
        if rows:
            await self.session.flush()
        return len(rows)

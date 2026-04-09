"""Notification ORM model — in-app bell-icon feed.

Distinct from ``AuditLogModel`` (compliance) and
``AutomationAuditModel`` (agent decision trail). This table is the
*user-facing* feed: "here's what you need to know about." Every row
is a thing the UI might want to interrupt the user about — a plan
generated, an approval auto-granted, a publish failed, an escalation
the agent handed back to a human.

One row per user (``user_id`` NOT NULL) so "mark as read" is per-user
even on shared tenants. If you want a broadcast, fan it out across
memberships at write time instead of using a tenant-wide row.
"""

from __future__ import annotations

import uuid

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from amplify.db.base import Base, TenantMixin, TimestampMixin


# Severity mirrors the dispatcher's Severity enum plus a "success"
# variant for positive confirmations (e.g. "plan generated") that
# Slack/email channels don't need but the UI wants to paint green.
NOTIFICATION_SEVERITIES = ("info", "success", "warning", "error")


class NotificationModel(Base, TimestampMixin, TenantMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        Index(
            "ix_notifications_user_unread",
            "tenant_id",
            "user_id",
            "read_at",
        ),
        Index(
            "ix_notifications_tenant_created",
            "tenant_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False
    )

    # Free-form event type — e.g. "automation.plan_generated",
    # "automation.publish_failed", "approval.auto_granted". Used for
    # icon + routing in the UI, not enforced at the DB level.
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)

    severity: Mapped[str] = mapped_column(String(20), default="info", nullable=False)

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="")

    # Optional deep-link target. The UI opens ``url`` when the user
    # clicks the notification — typically a route like
    # "/campaigns/<id>" or "/approvals/<id>".
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    # read_at is NULL until the user (or a mark-all-read) clears it.
    # The unread-count query filters on this column, so keep it
    # covered by ``ix_notifications_user_unread``.
    read_at: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)

    # Bag of extra context the UI might render (counts, snippets, etc).
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

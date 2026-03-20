"""Approval and ApprovalComment ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from amplify.db.base import Base, TimestampMixin, TenantMixin

if TYPE_CHECKING:
    from amplify.db.models.post import PostModel
    from amplify.db.models.asset import AssetModel
    from amplify.db.models.user import UserModel


class ApprovalModel(Base, TimestampMixin, TenantMixin):
    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_tenant_status", "tenant_id", "status"),
        Index("ix_approvals_tenant_action_type", "tenant_id", "action_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), index=True, nullable=False
    )

    # What action is being approved
    action_type: Mapped[str] = mapped_column(String(50), nullable=False, default="publish_post")
    entity_type: Mapped[str] = mapped_column(String(50), default="")
    entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    # Risk assessment
    risk_level: Mapped[str] = mapped_column(String(20), default="medium")
    risk_reasons: Mapped[list] = mapped_column(JSON, default=list)
    policy_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)

    # Action payload — replayed on approve
    action_payload: Mapped[dict] = mapped_column(JSON, default=dict)

    # Legacy FK references (optional, for post/asset quick joins)
    post_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("posts.id"), nullable=True
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assets.id"), nullable=True
    )

    # People
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    # State
    status: Mapped[str] = mapped_column(String(30), default="pending")
    notes: Mapped[str] = mapped_column(Text, default="")

    # Timestamps
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    post: Mapped[PostModel | None] = relationship()
    asset: Mapped[AssetModel | None] = relationship()
    requester: Mapped[UserModel | None] = relationship(foreign_keys=[requested_by])
    reviewer: Mapped[UserModel | None] = relationship(foreign_keys=[reviewed_by])
    comments: Mapped[list[ApprovalCommentModel]] = relationship(
        back_populates="approval", order_by="ApprovalCommentModel.created_at"
    )


class ApprovalCommentModel(Base, TimestampMixin):
    __tablename__ = "approval_comments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    approval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("approvals.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    approval: Mapped[ApprovalModel] = relationship(back_populates="comments")

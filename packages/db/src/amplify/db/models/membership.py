"""Membership ORM model — links users to tenants with roles."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from amplify.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from amplify.db.models.user import UserModel
    from amplify.db.models.tenant import TenantModel


class MembershipModel(Base, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="member")

    # Relationships
    user: Mapped[UserModel] = relationship(back_populates="memberships")
    tenant: Mapped[TenantModel] = relationship(back_populates="memberships")

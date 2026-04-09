"""Notification routes — in-app bell feed."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id, get_user_id
from app.schemas import (
    MessageResponse,
    NotificationResponse,
    UnreadCountResponse,
)
from amplify.core.services.notification_service import NotificationService
from amplify.db.models.notification import NotificationModel


router = APIRouter(prefix="/notifications", tags=["notifications"])


def _require_user_id(
    user_id: uuid.UUID | None = Depends(get_user_id),
) -> uuid.UUID:
    """Notifications are per-user; reject requests without a user context."""
    if user_id is None:
        raise HTTPException(status_code=401, detail="User context required")
    return user_id


@router.get("", response_model=list[NotificationResponse])
async def list_notifications(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID = Depends(_require_user_id),
    unread_only: bool = False,
    offset: int = 0,
    limit: int = Query(default=50, le=200),
):
    """List notifications for the current user, newest first."""
    stmt = (
        select(NotificationModel)
        .where(
            NotificationModel.tenant_id == tenant_id,
            NotificationModel.user_id == user_id,
        )
        .order_by(NotificationModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if unread_only:
        stmt = stmt.where(NotificationModel.read_at.is_(None))

    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/unread-count", response_model=UnreadCountResponse)
async def unread_count(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID = Depends(_require_user_id),
):
    """Badge counter for the bell icon."""
    count = (
        await db.execute(
            select(func.count(NotificationModel.id)).where(
                NotificationModel.tenant_id == tenant_id,
                NotificationModel.user_id == user_id,
                NotificationModel.read_at.is_(None),
            )
        )
    ).scalar_one()
    return UnreadCountResponse(unread=int(count or 0))


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID = Depends(_require_user_id),
):
    """Mark a single notification as read."""
    service = NotificationService(db, tenant_id)
    row = await service.mark_read(notification_id, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    return row


@router.post("/read-all", response_model=MessageResponse)
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID = Depends(_require_user_id),
):
    """Mark every unread notification for the current user as read."""
    service = NotificationService(db, tenant_id)
    count = await service.mark_all_read(user_id)
    return MessageResponse(message=f"Marked {count} notification(s) as read")

"""Automation routes — agent decision trail + (future) tick controls.

Today this only exposes the audit log so the user can see what the
autonomy worker has been doing. Future siblings: pause/resume controls,
manual tick trigger, decider preview.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id
from app.middleware.rbac import require_role
from app.schemas import AutomationAuditResponse
from amplify.db.models.automation_audit import (
    AUTOMATION_ACTIONS,
    AutomationAuditModel,
)


router = APIRouter(prefix="/automation", tags=["automation"])


@router.get(
    "/audit",
    response_model=list[AutomationAuditResponse],
    dependencies=[Depends(require_role("admin"))],
)
async def list_automation_audit(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    action: str | None = Query(
        default=None,
        description=(
            "Optional filter by action verb. Must be one of "
            f"{AUTOMATION_ACTIONS} when provided."
        ),
    ),
    offset: int = 0,
    limit: int = Query(default=100, le=500),
):
    """List automation audit entries for the current tenant.

    Newest first. Admin+ only — same gate as the human audit log because
    it can reveal scheduling cadence and tenant configuration.
    """
    stmt = (
        select(AutomationAuditModel)
        .where(AutomationAuditModel.tenant_id == tenant_id)
        .order_by(AutomationAuditModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if action and action in AUTOMATION_ACTIONS:
        stmt = stmt.where(AutomationAuditModel.action == action)

    result = await db.execute(stmt)
    return list(result.scalars().all())

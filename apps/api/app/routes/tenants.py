"""Tenant routes — profile, update, members, audit log."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id, get_user_id, get_audit_service
from app.middleware.rbac import require_role
from app.schemas import (
    TenantResponse,
    TenantUpdateRequest,
    UserResponse,
    AuditLogResponse,
)
from app.services.audit_service import AuditService
from amplify.db.models.tenant import TenantModel
from amplify.db.models.membership import MembershipModel
from amplify.db.models.user import UserModel
from amplify.db.models.audit_log import AuditLogModel

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.get("/me", response_model=TenantResponse)
async def get_current_tenant(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Get the current tenant profile."""
    stmt = select(TenantModel).where(TenantModel.id == tenant_id)
    result = await db.execute(stmt)
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


@router.put(
    "/me",
    response_model=TenantResponse,
    dependencies=[Depends(require_role("owner"))],
)
async def update_current_tenant(
    body: TenantUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Update the current tenant profile. Owner only."""
    stmt = select(TenantModel).where(TenantModel.id == tenant_id)
    result = await db.execute(stmt)
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(tenant, field, value)

    await db.flush()
    await db.refresh(tenant)

    await audit.log(
        action="tenant.updated",
        entity_type="tenant",
        entity_id=tenant.id,
        user_id=user_id,
        changes=updates,
    )

    return tenant


@router.get(
    "/me/members",
    response_model=list[UserResponse],
    dependencies=[Depends(require_role("admin"))],
)
async def list_tenant_members(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """List all members of the current tenant. Admin+ only."""
    stmt = (
        select(UserModel)
        .join(MembershipModel, MembershipModel.user_id == UserModel.id)
        .where(MembershipModel.tenant_id == tenant_id)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get(
    "/me/audit-log",
    response_model=list[AuditLogResponse],
    dependencies=[Depends(require_role("admin"))],
)
async def list_audit_log(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    offset: int = 0,
    limit: int = 100,
):
    """List audit log entries for the current tenant. Admin+ only."""
    stmt = (
        select(AuditLogModel)
        .where(AuditLogModel.tenant_id == tenant_id)
        .order_by(AuditLogModel.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())

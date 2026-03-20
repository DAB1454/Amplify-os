"""Tenant service – CRUD operations for tenants."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.tenant import TenantModel

if TYPE_CHECKING:
    pass


class TenantService:
    """Service layer for tenant operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_tenant(self, tenant_id: uuid.UUID) -> TenantModel | None:
        """Retrieve a tenant by ID."""
        stmt = select(TenantModel).where(TenantModel.id == tenant_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def create_tenant(self, name: str, slug: str) -> TenantModel:
        """Create a new tenant with default settings."""
        tenant = TenantModel(
            id=uuid.uuid4(),
            name=name,
            slug=slug,
        )
        self.db.add(tenant)
        await self.db.flush()
        # Refresh so server-generated columns (created_at, updated_at) are
        # loaded before any downstream serialization — avoids MissingGreenlet.
        await self.db.refresh(tenant)
        return tenant

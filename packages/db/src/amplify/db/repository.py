"""Generic tenant-scoped base repository."""

from __future__ import annotations

import uuid
from typing import Generic, Type, TypeVar

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


class BaseRepository(Generic[T]):
    """Base repository that automatically scopes all queries to a tenant."""

    def __init__(
        self,
        session: AsyncSession,
        model: Type[T],
        tenant_id: uuid.UUID | str,
    ) -> None:
        self.session = session
        self.model = model
        self.tenant_id = uuid.UUID(str(tenant_id))

    async def get(self, id: uuid.UUID) -> T | None:
        """Get a single entity by id, scoped to tenant."""
        stmt = select(self.model).where(
            self.model.id == id,
            self.model.tenant_id == self.tenant_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self, offset: int = 0, limit: int = 100, **filters: object
    ) -> list[T]:
        """List entities for the tenant with optional filters."""
        stmt = (
            select(self.model)
            .where(self.model.tenant_id == self.tenant_id)
            .offset(offset)
            .limit(limit)
        )
        for attr, value in filters.items():
            if hasattr(self.model, attr):
                stmt = stmt.where(getattr(self.model, attr) == value)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create(self, **kwargs: object) -> T:
        """Create a new entity, injecting tenant_id automatically."""
        kwargs["tenant_id"] = self.tenant_id
        if "id" not in kwargs:
            kwargs["id"] = uuid.uuid4()
        entity = self.model(**kwargs)
        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def update(self, id: uuid.UUID, **kwargs: object) -> T | None:
        """Update an entity by id, scoped to tenant."""
        stmt = (
            update(self.model)
            .where(
                self.model.id == id,
                self.model.tenant_id == self.tenant_id,
            )
            .values(**kwargs)
            .returning(self.model)
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is not None:
            await self.session.flush()
        return row

    async def delete(self, id: uuid.UUID) -> bool:
        """Delete an entity by id, scoped to tenant. Returns True if deleted."""
        stmt = (
            delete(self.model)
            .where(
                self.model.id == id,
                self.model.tenant_id == self.tenant_id,
            )
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0

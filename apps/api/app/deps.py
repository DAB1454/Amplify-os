"""FastAPI dependency injection."""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import AsyncGenerator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.services.auth_service import AuthService
from app.services.audit_service import AuditService


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session."""
    async_session = request.app.state.async_session
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_redis(request: Request):
    """Return the Redis client from app state."""
    return request.app.state.redis


def get_tenant_id(request: Request) -> uuid.UUID:
    """Extract tenant ID from request state (set by TenantMiddleware)."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="No tenant context")
    return tenant_id


def get_user_id(request: Request) -> uuid.UUID | None:
    """Extract user ID from request state."""
    return getattr(request.state, "user_id", None)


def get_role(request: Request) -> str:
    """Extract user role from request state."""
    return getattr(request.state, "role", "member")


async def get_auth_service(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthService:
    """Return an AuthService instance."""
    return AuthService(db, settings.jwt_secret, settings.jwt_algorithm)


async def get_audit_service(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> AuditService:
    """Return an AuditService scoped to the current tenant."""
    return AuditService(db, tenant_id)


async def get_oauth_service(
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    settings: Settings = Depends(get_settings),
):
    """Return an OAuthService instance."""
    from app.services.oauth_service import OAuthService
    return OAuthService(db, redis, settings)

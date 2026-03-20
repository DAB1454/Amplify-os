"""Authentication routes — register, login, refresh, me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_auth_service, get_user_id
from app.schemas import (
    RegisterRequest,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    body: RegisterRequest,
    auth: AuthService = Depends(get_auth_service),
):
    """Create a new user, tenant, and owner membership. Returns token pair."""
    try:
        user, tenant, membership = await auth.register(
            email=body.email,
            password=body.password,
            tenant_name=body.tenant_name,
            display_name=body.display_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    tokens = auth.create_token_pair(user.id, tenant.id, membership.role)
    return tokens


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    auth: AuthService = Depends(get_auth_service),
):
    """Validate credentials and return a token pair."""
    try:
        user, tenant, role = await auth.login(body.email, body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    tokens = auth.create_token_pair(user.id, tenant.id, role)
    return tokens


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    auth: AuthService = Depends(get_auth_service),
):
    """Exchange a refresh token for a new token pair."""
    try:
        tokens = await auth.refresh_tokens(body.refresh_token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return tokens


@router.get("/me", response_model=UserResponse)
async def me(
    user_id=Depends(get_user_id),
    auth: AuthService = Depends(get_auth_service),
):
    """Return the current authenticated user's info."""
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = await auth.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    return user

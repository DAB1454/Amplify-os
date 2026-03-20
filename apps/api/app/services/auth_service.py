"""Authentication service — registration, login, JWT token management."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.user import UserModel
from amplify.db.models.tenant import TenantModel
from amplify.db.models.membership import MembershipModel


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Token expiry durations
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


class AuthService:
    """Handles user registration, login, and JWT token lifecycle."""

    def __init__(self, session: AsyncSession, jwt_secret: str, jwt_algorithm: str = "HS256"):
        self.session = session
        self.jwt_secret = jwt_secret
        self.jwt_algorithm = jwt_algorithm

    # ── Password helpers ──────────────────────────────────────────

    @staticmethod
    def hash_password(password: str) -> str:
        return pwd_context.hash(password)

    @staticmethod
    def verify_password(plain: str, hashed: str) -> bool:
        return pwd_context.verify(plain, hashed)

    # ── Token helpers ─────────────────────────────────────────────

    def create_access_token(self, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user_id),
            "tenant_id": str(tenant_id),
            "role": role,
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        }
        return jwt.encode(payload, self.jwt_secret, algorithm=self.jwt_algorithm)

    def create_refresh_token(self, user_id: uuid.UUID, tenant_id: uuid.UUID) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "sub": str(user_id),
            "tenant_id": str(tenant_id),
            "type": "refresh",
            "iat": now,
            "exp": now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        }
        return jwt.encode(payload, self.jwt_secret, algorithm=self.jwt_algorithm)

    def decode_token(self, token: str) -> dict:
        """Decode and validate a JWT. Raises jwt.PyJWTError on failure."""
        return jwt.decode(token, self.jwt_secret, algorithms=[self.jwt_algorithm])

    # ── User lookup ───────────────────────────────────────────────

    async def get_user_by_email(self, email: str) -> UserModel | None:
        stmt = select(UserModel).where(UserModel.email == email)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: uuid.UUID) -> UserModel | None:
        stmt = select(UserModel).where(UserModel.id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ── Registration ──────────────────────────────────────────────

    async def register(
        self,
        email: str,
        password: str,
        tenant_name: str,
        display_name: str | None = None,
    ) -> tuple[UserModel, TenantModel, MembershipModel]:
        """Register a new user, create their tenant, and assign owner membership.

        Raises ValueError if email already exists.
        """
        existing = await self.get_user_by_email(email)
        if existing:
            raise ValueError("Email already registered")

        # Create tenant
        slug = tenant_name.lower().replace(" ", "-")
        # Ensure unique slug
        slug_check = await self.session.execute(
            select(TenantModel).where(TenantModel.slug == slug)
        )
        if slug_check.scalar_one_or_none():
            slug = f"{slug}-{uuid.uuid4().hex[:6]}"

        tenant = TenantModel(
            id=uuid.uuid4(),
            name=tenant_name,
            slug=slug,
        )
        self.session.add(tenant)

        # Create user
        user = UserModel(
            id=uuid.uuid4(),
            email=email,
            hashed_password=self.hash_password(password),
            display_name=display_name,
        )
        self.session.add(user)

        # Create owner membership
        membership = MembershipModel(
            id=uuid.uuid4(),
            user_id=user.id,
            tenant_id=tenant.id,
            role="owner",
        )
        self.session.add(membership)

        await self.session.flush()
        # Refresh so server-generated columns (created_at, updated_at) are
        # loaded — prevents MissingGreenlet if callers access those attrs.
        for obj in (user, tenant, membership):
            await self.session.refresh(obj)
        return user, tenant, membership

    # ── Login ─────────────────────────────────────────────────────

    async def login(self, email: str, password: str) -> tuple[UserModel, TenantModel, str]:
        """Authenticate user and return user, tenant, and role.

        Raises ValueError on invalid credentials.
        """
        user = await self.get_user_by_email(email)
        if not user or not self.verify_password(password, user.hashed_password):
            raise ValueError("Invalid email or password")

        if not user.is_active:
            raise ValueError("Account is deactivated")

        # Get first membership (primary tenant)
        stmt = select(MembershipModel).where(MembershipModel.user_id == user.id).limit(1)
        result = await self.session.execute(stmt)
        membership = result.scalar_one_or_none()

        if not membership:
            raise ValueError("User has no tenant membership")

        stmt = select(TenantModel).where(TenantModel.id == membership.tenant_id)
        result = await self.session.execute(stmt)
        tenant = result.scalar_one_or_none()

        if not tenant or not tenant.is_active:
            raise ValueError("Tenant is deactivated")

        return user, tenant, membership.role

    # ── Token pair generation ─────────────────────────────────────

    def create_token_pair(
        self, user_id: uuid.UUID, tenant_id: uuid.UUID, role: str
    ) -> dict:
        return {
            "access_token": self.create_access_token(user_id, tenant_id, role),
            "refresh_token": self.create_refresh_token(user_id, tenant_id),
            "token_type": "bearer",
            "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        }

    # ── Refresh ───────────────────────────────────────────────────

    async def refresh_tokens(self, refresh_token: str) -> dict:
        """Validate refresh token and issue new token pair.

        Raises ValueError on invalid/expired token.
        """
        try:
            payload = self.decode_token(refresh_token)
        except jwt.PyJWTError as e:
            raise ValueError(f"Invalid refresh token: {e}")

        if payload.get("type") != "refresh":
            raise ValueError("Token is not a refresh token")

        user_id = uuid.UUID(payload["sub"])
        tenant_id = uuid.UUID(payload["tenant_id"])

        user = await self.get_user_by_id(user_id)
        if not user or not user.is_active:
            raise ValueError("User not found or deactivated")

        # Get role from membership
        stmt = select(MembershipModel).where(
            MembershipModel.user_id == user_id,
            MembershipModel.tenant_id == tenant_id,
        )
        result = await self.session.execute(stmt)
        membership = result.scalar_one_or_none()

        if not membership:
            raise ValueError("Membership not found")

        return self.create_token_pair(user_id, tenant_id, membership.role)

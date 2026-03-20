"""Test fixtures for integration tests.

Uses SQLite in-memory database and overrides FastAPI dependencies.
No Postgres or Redis required.
"""

import json
import os
import uuid
from datetime import date, datetime, time, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _json_serializer(obj):
    """JSON serializer that handles UUID and date/time types for SQLite."""
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (datetime, date, time)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _dumps(value):
    return json.dumps(value, default=_json_serializer)

# ── Env must be set BEFORE any app import ────────────────────────
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["DEPLOYMENT_MODE"] = "local"
os.environ["REDIS_URL"] = ""

from amplify.db.base import Base                       # noqa: E402
import amplify.db.models  # noqa: E402, F401  – register all ORM models

# Use aiosqlite for async SQLite
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

# Fixed test IDs matching TenantMiddleware local mode
TEST_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

JWT_SECRET = "change-me-in-production"
JWT_ALGORITHM = "HS256"


# ── Database fixtures ────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_engine():
    """Create a test database engine with all tables."""
    engine = create_async_engine(TEST_DB_URL, echo=False, json_serializer=_dumps)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session_factory(db_engine):
    """Create a session factory for tests."""
    return async_sessionmaker(
        bind=db_engine, class_=AsyncSession, expire_on_commit=False
    )


@pytest_asyncio.fixture
async def db_session(db_session_factory):
    """Yield a database session for direct DB operations in tests."""
    async with db_session_factory() as session:
        yield session


# ── App fixtures ─────────────────────────────────────────────────


@pytest_asyncio.fixture
async def app(db_session_factory):
    """Create a test FastAPI application with SQLite backend.

    Bypasses the normal lifespan (which connects to Postgres/Redis)
    by building the app without lifespan and injecting the test DB
    session factory directly.
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from app.config import Settings
    from app.middleware.tenant import TenantMiddleware
    from app.routes.health import router as health_router
    from app.routes.auth import router as auth_router
    from app.routes.tenants import router as tenants_router
    from app.routes.artists import router as artists_router
    from app.routes.releases import router as releases_router
    from app.routes.channels import router as channels_router
    from app.routes.campaigns import router as campaigns_router
    from app.routes.calendar_items import router as calendar_items_router
    from app.routes.assets import router as assets_router
    from app.routes.posts import router as posts_router
    from app.routes.approvals import router as approvals_router
    from app.routes.analytics import router as analytics_router
    from app.routes.billing import router as billing_router
    from app.routes.webhooks import router as webhooks_router
    from app.routes.redirects import router as redirects_router
    from app.routes.redirects import public_router as redirect_public_router
    from app.routes.oauth import router as oauth_router
    from app.routes.oauth import callback_router as oauth_callback_router
    from app.routes.learning import router as learning_router
    from app.routes.features import router as features_router
    from app.routes.outcomes import router as outcomes_router

    settings = Settings()

    application = FastAPI(title="Amplify-OS API Test")

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(
        TenantMiddleware,
        deployment_mode="local",
        jwt_secret=settings.jwt_secret,
        jwt_algorithm=settings.jwt_algorithm,
    )

    prefix = "/api/v1"
    application.include_router(health_router)
    application.include_router(auth_router, prefix=prefix)
    application.include_router(tenants_router, prefix=prefix)
    application.include_router(artists_router, prefix=prefix)
    application.include_router(releases_router, prefix=prefix)
    application.include_router(channels_router, prefix=prefix)
    application.include_router(campaigns_router, prefix=prefix)
    application.include_router(calendar_items_router, prefix=prefix)
    application.include_router(assets_router, prefix=prefix)
    application.include_router(posts_router, prefix=prefix)
    application.include_router(approvals_router, prefix=prefix)
    application.include_router(analytics_router, prefix=prefix)
    application.include_router(billing_router, prefix=prefix)
    application.include_router(webhooks_router, prefix=prefix)
    application.include_router(redirects_router, prefix=prefix)
    application.include_router(learning_router, prefix=prefix)
    application.include_router(features_router, prefix=prefix)
    application.include_router(outcomes_router, prefix=prefix)
    application.include_router(oauth_router, prefix=prefix)
    application.include_router(redirect_public_router)
    application.include_router(oauth_callback_router)

    # Inject test DB and disable Redis
    application.state.settings = settings
    application.state.async_session = db_session_factory
    application.state.redis = None

    return application


@pytest_asyncio.fixture
async def cloud_app(db_session_factory):
    """Create a test app in cloud deployment mode (JWT auth required)."""
    from fastapi import FastAPI
    from app.config import Settings
    from app.middleware.tenant import TenantMiddleware
    from app.routes.health import router as health_router
    from app.routes.auth import router as auth_router
    from app.routes.tenants import router as tenants_router
    from app.routes.artists import router as artists_router
    from app.routes.releases import router as releases_router
    from app.routes.campaigns import router as campaigns_router
    from app.routes.calendar_items import router as calendar_items_router
    from app.routes.posts import router as posts_router

    settings = Settings()

    application = FastAPI(title="Amplify-OS API Test (Cloud)")

    application.add_middleware(
        TenantMiddleware,
        deployment_mode="cloud",
        jwt_secret=settings.jwt_secret,
        jwt_algorithm=settings.jwt_algorithm,
    )

    prefix = "/api/v1"
    application.include_router(health_router)
    application.include_router(auth_router, prefix=prefix)
    application.include_router(tenants_router, prefix=prefix)
    application.include_router(artists_router, prefix=prefix)
    application.include_router(releases_router, prefix=prefix)
    application.include_router(campaigns_router, prefix=prefix)
    application.include_router(calendar_items_router, prefix=prefix)
    application.include_router(posts_router, prefix=prefix)

    application.state.settings = settings
    application.state.async_session = db_session_factory
    application.state.redis = None

    return application


@pytest_asyncio.fixture
async def client(app):
    """Async HTTP test client (local mode)."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=True,
    ) as ac:
        yield ac


@pytest_asyncio.fixture
async def cloud_client(cloud_app):
    """Async HTTP test client (cloud mode — JWT required)."""
    async with AsyncClient(
        transport=ASGITransport(app=cloud_app),
        base_url="http://test",
        follow_redirects=True,
    ) as ac:
        yield ac


# ── Seed data fixtures ───────────────────────────────────────────


@pytest_asyncio.fixture
async def seed_tenant(db_session):
    """Seed the local tenant so tenant lookups work in local mode."""
    from amplify.db.models.tenant import TenantModel
    from amplify.db.models.user import UserModel
    from amplify.db.models.membership import MembershipModel

    tenant = TenantModel(
        id=TEST_TENANT_ID,
        name="Test Studio",
        slug="test-studio",
    )
    db_session.add(tenant)

    user = UserModel(
        id=TEST_USER_ID,
        email="test@amplify.local",
        hashed_password="not-a-real-hash",
        display_name="Test User",
    )
    db_session.add(user)

    membership = MembershipModel(
        id=uuid.uuid4(),
        user_id=TEST_USER_ID,
        tenant_id=TEST_TENANT_ID,
        role="owner",
    )
    db_session.add(membership)

    await db_session.commit()
    return tenant, user


@pytest_asyncio.fixture
async def seed_artist(db_session, seed_tenant):
    """Seed a test artist."""
    from amplify.db.models.artist import ArtistModel

    artist = ArtistModel(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        name="Luna Vega",
        slug="luna-vega",
        bio="Indie electronic artist",
        genre="electronic",
    )
    db_session.add(artist)
    await db_session.commit()
    return artist


@pytest_asyncio.fixture
async def seed_channel(db_session, seed_artist):
    """Seed a test channel connection (depends on seed_artist which depends on seed_tenant)."""
    from amplify.db.models.channel import ChannelConnectionModel

    channel = ChannelConnectionModel(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        artist_id=seed_artist.id,
        platform="instagram",
        display_name="@lunavega",
        is_active=True,
    )
    db_session.add(channel)
    await db_session.commit()
    return channel

from contextlib import asynccontextmanager

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
from app.routes.admin import router as admin_router
from app.routes.assisted_tasks import router as assisted_tasks_router
from app.routes.campaign_templates import router as campaign_templates_router
from app.routes.onboarding import router as onboarding_router
from app.routes.redirects import router as redirects_router
from app.routes.redirects import public_router as redirect_public_router
from app.routes.queue_admin import router as queue_admin_router
from app.routes.oauth import router as oauth_router
from app.routes.oauth import callback_router as oauth_callback_router
from app.routes.learning import router as learning_router
from app.routes.features import router as features_router
from app.routes.outcomes import router as outcomes_router
from app.routes.prompts import router as prompts_router
from app.routes.evaluations import router as evaluations_router
from app.routes.safety import router as safety_router
from app.routes.cohorts import router as cohorts_router
from app.routes.global_priors import router as global_priors_router
from app.routes.intelligence import router as intelligence_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    settings = Settings()
    app.state.settings = settings

    # Initialize async DB engine
    from amplify.db.session import create_engine, create_session_factory

    engine = create_engine(settings.database_url)
    app.state.db_engine = engine
    app.state.async_session = create_session_factory(engine)

    # Auto-create tables for SQLite (Postgres uses Alembic migrations)
    if settings.database_url.startswith("sqlite"):
        from amplify.db.base import Base
        import amplify.db.models  # noqa: F401 — register all ORM models
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # Initialize Redis connection (graceful fallback)
    try:
        import redis.asyncio as aioredis
        app.state.redis = aioredis.from_url(settings.redis_url)
    except Exception:
        app.state.redis = None

    yield

    # Shutdown
    if hasattr(app.state, "db_engine"):
        await app.state.db_engine.dispose()
    if hasattr(app.state, "redis") and app.state.redis:
        await app.state.redis.close()


def create_app() -> FastAPI:
    """Application factory."""
    settings = Settings()

    application = FastAPI(
        title="Amplify-OS API",
        description="Agentic music marketing platform API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Tenant isolation middleware
    application.add_middleware(
        TenantMiddleware,
        deployment_mode=settings.deployment_mode,
        jwt_secret=settings.jwt_secret,
        jwt_algorithm=settings.jwt_algorithm,
    )

    # Mount routers
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
    application.include_router(assisted_tasks_router, prefix=prefix)
    application.include_router(campaign_templates_router, prefix=prefix)
    application.include_router(onboarding_router, prefix=prefix)
    application.include_router(admin_router, prefix=prefix)
    application.include_router(queue_admin_router, prefix=prefix)
    application.include_router(redirects_router, prefix=prefix)
    application.include_router(learning_router, prefix=prefix)
    application.include_router(features_router, prefix=prefix)
    application.include_router(outcomes_router, prefix=prefix)
    application.include_router(prompts_router, prefix=prefix)
    application.include_router(evaluations_router, prefix=prefix)
    application.include_router(safety_router, prefix=prefix)
    application.include_router(cohorts_router, prefix=prefix)
    application.include_router(global_priors_router, prefix=prefix)
    application.include_router(intelligence_router, prefix=prefix)
    application.include_router(oauth_router, prefix=prefix)
    application.include_router(redirect_public_router)
    application.include_router(oauth_callback_router)

    return application


app = create_app()

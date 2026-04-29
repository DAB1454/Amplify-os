from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from app.config import Settings
from app.deps import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
@router.get("/healthz")
async def healthz(request: Request, settings: Settings = Depends(get_settings)):
    checks: dict = {}

    # Database connectivity
    try:
        async_session = request.app.state.async_session
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"

    # Redis connectivity
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            await redis.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {type(exc).__name__}"
    else:
        checks["redis"] = "not configured"

    all_ok = all(v == "ok" for v in checks.values())

    return {
        "status": "ok" if all_ok else "degraded",
        "mode": settings.deployment_mode,
        "version": "0.1.0",
        "checks": checks,
    }

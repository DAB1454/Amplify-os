from fastapi import APIRouter, Depends

from app.config import Settings
from app.deps import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
@router.get("/healthz")
async def healthz(settings: Settings = Depends(get_settings)):
    return {
        "status": "ok",
        "mode": settings.deployment_mode,
        "version": "0.1.0",
    }

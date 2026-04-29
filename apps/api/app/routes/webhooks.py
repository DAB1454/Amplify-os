from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/{platform}")
async def receive_webhook(platform: str, request: Request):
    """Receive webhook callbacks from external platforms."""
    return JSONResponse(
        status_code=501,
        content={"platform": platform, "message": "Webhook handler not yet implemented"},
    )

from fastapi import APIRouter, Request

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/{platform}")
async def receive_webhook(platform: str, request: Request):
    """Receive webhook callbacks from external platforms."""
    return {"platform": platform, "message": "Webhook received — not yet implemented"}

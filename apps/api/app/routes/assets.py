from fastapi import APIRouter, status

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("")
@router.get("/")
async def list_assets():
    return []


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_asset():
    return {"message": "Asset created — not yet implemented"}


@router.get("/{asset_id}")
async def get_asset(asset_id: str):
    return {"id": asset_id, "message": "Asset detail — not yet implemented"}


@router.put("/{asset_id}")
async def update_asset(asset_id: str):
    return {"id": asset_id, "message": "Asset updated — not yet implemented"}


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(asset_id: str):
    return None

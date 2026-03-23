"""Channel connection routes with integration mode metadata."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id
from amplify.core.domain.post import PLATFORM_INTEGRATION_MODE, PLATFORM_METADATA, Platform
from amplify.db.models.channel import ChannelConnectionModel

router = APIRouter(prefix="/channels", tags=["channels"])


@router.get("/platforms")
async def list_platforms():
    """Return all supported platforms with their integration mode and capabilities."""
    return [
        {
            "platform": p.value,
            **meta,
        }
        for p, meta in PLATFORM_METADATA.items()
    ]


@router.get("")
@router.get("/")
async def list_channels(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    result = await db.execute(
        select(ChannelConnectionModel)
        .where(ChannelConnectionModel.tenant_id == tenant_id)
        .order_by(ChannelConnectionModel.created_at.desc())
    )
    channels = result.scalars().all()
    return [_channel_to_dict(ch) for ch in channels]


@router.post("", status_code=status.HTTP_201_CREATED)
@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_channel(
    body: dict,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    platform_str = body.get("platform", "")
    try:
        platform = Platform(platform_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown platform: {platform_str}")

    mode = PLATFORM_INTEGRATION_MODE.get(platform, "assisted")
    mode_str = mode.value if hasattr(mode, "value") else str(mode)

    # Resolve artist_id — auto-pick tenant's first artist if not provided
    artist_id = body.get("artist_id")
    if not artist_id or artist_id == "00000000-0000-0000-0000-000000000000":
        from amplify.db.models.artist import ArtistModel
        result = await db.execute(
            select(ArtistModel.id).where(ArtistModel.tenant_id == tenant_id).limit(1)
        )
        artist_id = result.scalar_one_or_none()
        if not artist_id:
            raise HTTPException(status_code=400, detail="No artist found. Create an artist first.")
    else:
        artist_id = uuid.UUID(artist_id) if isinstance(artist_id, str) else artist_id

    channel = ChannelConnectionModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        artist_id=artist_id,
        platform=platform_str,
        integration_mode=mode_str,
        platform_account_id=body.get("platform_account_id"),
        platform_url=body.get("platform_url"),
        display_name=body.get("display_name"),
        access_token=body.get("access_token"),
        refresh_token=body.get("refresh_token"),
        settings=body.get("settings", {}),
    )
    db.add(channel)
    await db.commit()
    await db.refresh(channel)
    return _channel_to_dict(channel)


@router.get("/{channel_id}")
async def get_channel(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    result = await db.execute(
        select(ChannelConnectionModel).where(
            ChannelConnectionModel.id == channel_id,
            ChannelConnectionModel.tenant_id == tenant_id,
        )
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    return _channel_to_dict(channel)


@router.put("/{channel_id}")
async def update_channel(
    channel_id: uuid.UUID,
    body: dict,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    result = await db.execute(
        select(ChannelConnectionModel).where(
            ChannelConnectionModel.id == channel_id,
            ChannelConnectionModel.tenant_id == tenant_id,
        )
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    for field in ("display_name", "platform_account_id", "platform_url", "is_active", "settings"):
        if field in body:
            setattr(channel, field, body[field])

    await db.commit()
    await db.refresh(channel)
    return _channel_to_dict(channel)


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    result = await db.execute(
        select(ChannelConnectionModel).where(
            ChannelConnectionModel.id == channel_id,
            ChannelConnectionModel.tenant_id == tenant_id,
        )
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    await db.delete(channel)
    await db.commit()


def _derive_connection_status(ch: ChannelConnectionModel) -> str:
    """Derive a human-readable connection status from channel state."""
    if not ch.is_active:
        return "disconnected"
    if not ch.access_token:
        return "not_connected"
    # Check last health status first
    if ch.last_health_status in ("expired", "revoked", "error"):
        return ch.last_health_status
    # Check token expiry
    if ch.token_expires_at:
        from datetime import datetime
        if datetime.utcnow() >= ch.token_expires_at:
            return "expired"
    return "connected"


def _channel_to_dict(ch: ChannelConnectionModel) -> dict:
    platform_str = ch.platform
    try:
        platform = Platform(platform_str)
        meta = PLATFORM_METADATA.get(platform, {})
    except ValueError:
        meta = {}

    # Use DB capabilities if present, fall back to platform metadata
    db_capabilities = getattr(ch, "capabilities", None) or {}
    platform_capabilities = meta.get("capabilities", [])

    return {
        "id": str(ch.id),
        "tenant_id": str(ch.tenant_id),
        "artist_id": str(ch.artist_id),
        "platform": ch.platform,
        "integration_mode": ch.integration_mode,
        "platform_account_id": ch.platform_account_id,
        "platform_url": getattr(ch, "platform_url", None),
        "display_name": ch.display_name,
        "is_active": ch.is_active,
        "connection_status": _derive_connection_status(ch),
        "granted_scopes": getattr(ch, "granted_scopes", None) or [],
        "capabilities": db_capabilities if db_capabilities else platform_capabilities,
        "mode_label": meta.get("mode", ch.integration_mode),
        "mode_description": meta.get("description", ""),
        "settings": ch.settings or {},
        "last_health_check_at": ch.last_health_check_at.isoformat() if getattr(ch, "last_health_check_at", None) else None,
        "created_at": ch.created_at.isoformat() if ch.created_at else None,
        "updated_at": ch.updated_at.isoformat() if ch.updated_at else None,
    }

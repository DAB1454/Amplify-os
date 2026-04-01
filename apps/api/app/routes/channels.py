"""Channel connection routes with integration mode metadata."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_settings, get_tenant_id
from app.config import Settings
from amplify.core.domain.post import PLATFORM_INTEGRATION_MODE, PLATFORM_METADATA, Platform
from amplify.db.models.channel import ChannelConnectionModel
from amplify.db.models.post import PostModel

logger = logging.getLogger(__name__)

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


@router.post("/{channel_id}/import-posts")
async def import_posts_from_platform(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    settings: Settings = Depends(get_settings),
):
    """Fetch published posts from a platform account and import them as post records."""
    result = await db.execute(
        select(ChannelConnectionModel).where(
            ChannelConnectionModel.id == channel_id,
            ChannelConnectionModel.tenant_id == tenant_id,
        )
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    if not channel.access_token:
        raise HTTPException(status_code=400, detail="Channel not connected")

    # Decrypt token
    from amplify.adapters.crypto import TokenEncryptor
    from amplify.adapters.token_store import TokenStore
    encryptor = TokenEncryptor(
        primary_key=settings.token_encryption_key,
        old_keys=settings.token_encryption_old_keys,
        deployment_mode=settings.deployment_mode,
    )
    tokens = TokenStore.from_channel_connection(channel, encryptor)

    platform = channel.platform
    imported = 0
    skipped = 0
    errors = []

    try:
        if platform == "instagram":
            imported, skipped, errors = await _import_instagram(
                db, tokens.access_token, channel.platform_account_id or "",
                channel, tenant_id,
            )
        elif platform == "youtube":
            imported, skipped, errors = await _import_youtube(
                db, tokens.access_token, channel, tenant_id,
            )
        elif platform == "tiktok":
            raise HTTPException(
                status_code=400,
                detail="TikTok import requires video.list scope which is not currently enabled. "
                       "Import will be available after app review.",
            )
        else:
            raise HTTPException(status_code=400, detail=f"Import not supported for {platform}")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Import failed for channel %s: %s", channel_id, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Import failed: {exc}")

    await db.commit()
    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors[:10],
    }


async def _import_instagram(
    db: AsyncSession,
    access_token: str,
    account_id: str,
    channel: ChannelConnectionModel,
    tenant_id: uuid.UUID,
) -> tuple[int, int, list[str]]:
    """Fetch all media from Instagram and create post records."""
    imported = 0
    skipped = 0
    errors: list[str] = []
    GRAPH_API = "https://graph.instagram.com/v21.0"

    async with httpx.AsyncClient(timeout=30) as client:
        url = f"{GRAPH_API}/me/media"
        params = {
            "fields": "id,caption,media_type,media_url,thumbnail_url,timestamp,permalink,like_count,comments_count",
            "access_token": access_token,
            "limit": "50",
        }

        while url:
            resp = await client.get(url, params=params)
            if resp.status_code >= 400:
                errors.append(f"Instagram API error: {resp.text[:200]}")
                break

            data = resp.json()
            for item in data.get("data", []):
                platform_post_id = item.get("id", "")

                # Skip if already imported
                existing = await db.execute(
                    select(PostModel.id).where(
                        PostModel.platform_post_id == platform_post_id,
                        PostModel.tenant_id == tenant_id,
                    )
                )
                if existing.scalar_one_or_none():
                    skipped += 1
                    continue

                media_url = item.get("media_url") or item.get("thumbnail_url") or ""
                published_at = None
                if item.get("timestamp"):
                    try:
                        published_at = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")).replace(tzinfo=None)
                    except Exception:
                        pass

                post = PostModel(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    channel_id=channel.id,
                    platform="instagram",
                    status="published",
                    content_text=item.get("caption", ""),
                    media_urls=[media_url] if media_url else [],
                    platform_post_id=platform_post_id,
                    permalink=item.get("permalink", ""),
                    published_at=published_at,
                    engagement={
                        "likes": item.get("like_count", 0),
                        "comments": item.get("comments_count", 0),
                    },
                    action_type_label=item.get("media_type", "IMAGE").lower(),
                )
                db.add(post)
                imported += 1

            # Pagination
            next_url = data.get("paging", {}).get("next")
            if next_url:
                url = next_url
                params = {}  # next URL includes all params
            else:
                break

    return imported, skipped, errors


async def _import_youtube(
    db: AsyncSession,
    access_token: str,
    channel: ChannelConnectionModel,
    tenant_id: uuid.UUID,
) -> tuple[int, int, list[str]]:
    """Fetch all videos from YouTube channel and create post records."""
    imported = 0
    skipped = 0
    errors: list[str] = []
    YT_API = "https://www.googleapis.com/youtube/v3"
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=30) as client:
        # Get uploads playlist ID
        ch_resp = await client.get(
            f"{YT_API}/channels",
            headers=headers,
            params={"part": "contentDetails", "mine": "true"},
        )
        if ch_resp.status_code >= 400:
            return 0, 0, [f"YouTube channels API error: {ch_resp.text[:200]}"]

        items = ch_resp.json().get("items", [])
        if not items:
            return 0, 0, ["No YouTube channel found"]

        uploads_playlist = items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        if not uploads_playlist:
            return 0, 0, ["No uploads playlist found"]

        # List all videos from uploads playlist
        page_token = None
        video_ids: list[str] = []

        while True:
            params: dict = {
                "part": "snippet",
                "playlistId": uploads_playlist,
                "maxResults": "50",
            }
            if page_token:
                params["pageToken"] = page_token

            pl_resp = await client.get(f"{YT_API}/playlistItems", headers=headers, params=params)
            if pl_resp.status_code >= 400:
                errors.append(f"YouTube playlistItems error: {pl_resp.text[:200]}")
                break

            pl_data = pl_resp.json()
            for item in pl_data.get("items", []):
                vid_id = item.get("snippet", {}).get("resourceId", {}).get("videoId")
                if vid_id:
                    video_ids.append(vid_id)

            page_token = pl_data.get("nextPageToken")
            if not page_token:
                break

        # Fetch full details + stats for all videos in batches of 50
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            v_resp = await client.get(
                f"{YT_API}/videos",
                headers=headers,
                params={
                    "part": "snippet,statistics,status",
                    "id": ",".join(batch),
                },
            )
            if v_resp.status_code >= 400:
                errors.append(f"YouTube videos API error: {v_resp.text[:200]}")
                continue

            for v in v_resp.json().get("items", []):
                video_id = v.get("id", "")

                # Skip if already imported
                existing = await db.execute(
                    select(PostModel.id).where(
                        PostModel.platform_post_id == video_id,
                        PostModel.tenant_id == tenant_id,
                    )
                )
                if existing.scalar_one_or_none():
                    skipped += 1
                    continue

                snippet = v.get("snippet", {})
                stats = v.get("statistics", {})
                published_at = None
                if snippet.get("publishedAt"):
                    try:
                        published_at = datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00")).replace(tzinfo=None)
                    except Exception:
                        pass

                post = PostModel(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    channel_id=channel.id,
                    platform="youtube",
                    status="published",
                    content_text=snippet.get("description", ""),
                    media_urls=[snippet.get("thumbnails", {}).get("high", {}).get("url", "")],
                    platform_post_id=video_id,
                    permalink=f"https://youtu.be/{video_id}",
                    published_at=published_at,
                    engagement={
                        "views": int(stats.get("viewCount", 0)),
                        "likes": int(stats.get("likeCount", 0)),
                        "comments": int(stats.get("commentCount", 0)),
                    },
                    action_type_label=snippet.get("title", ""),
                )
                db.add(post)
                imported += 1

    return imported, skipped, errors


def _derive_connection_status(ch: ChannelConnectionModel) -> str:
    """Derive a human-readable connection status from channel state."""
    if not ch.is_active:
        return "disconnected"
    if not ch.access_token:
        return "not_connected"
    # Check last health status first (but not "expired" — we re-derive that below)
    if ch.last_health_status in ("revoked", "error"):
        return ch.last_health_status
    # Check token expiry
    if ch.token_expires_at:
        from datetime import datetime
        if datetime.utcnow() >= ch.token_expires_at:
            # If there's a refresh token, it's just stale — not dead
            if ch.refresh_token:
                return "connected"
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
        "avatar_url": getattr(ch, "avatar_url", None),
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

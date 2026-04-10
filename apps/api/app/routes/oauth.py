"""OAuth routes — connect, callback, refresh, health, disconnect."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.deps import get_db, get_redis, get_settings, get_tenant_id
from app.config import Settings

logger = logging.getLogger(__name__)

SUPPORTED_PLATFORMS = {"instagram", "youtube", "tiktok", "twitter"}

# ── Tenant-scoped router (under /api/v1) ──────────────────────

router = APIRouter(prefix="/channels", tags=["oauth"])


@router.get("/connect/{platform}")
async def connect_platform(
    platform: str,
    artist_id: uuid.UUID = Query(default=uuid.UUID("00000000-0000-0000-0000-000000000000")),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    settings: Settings = Depends(get_settings),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Generate an OAuth authorization URL for the given platform."""
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")

    # Auto-resolve artist_id if placeholder/null UUID is sent
    null_uuid = uuid.UUID("00000000-0000-0000-0000-000000000000")
    if artist_id == null_uuid:
        from amplify.db.models.artist import ArtistModel
        result = await db.execute(
            select(ArtistModel.id).where(ArtistModel.tenant_id == tenant_id).limit(1)
        )
        resolved = result.scalar_one_or_none()
        if not resolved:
            raise HTTPException(status_code=400, detail="No artist found. Create an artist first.")
        artist_id = resolved

    if settings.is_local:
        # Check if credentials are configured
        has_creds = _has_platform_creds(platform, settings)
        if not has_creds:
            return {
                "auth_url": None,
                "dry_run": True,
                "message": f"OAuth for {platform} is not configured in local mode. "
                           f"Set {platform.upper()}_CLIENT_ID and {platform.upper()}_CLIENT_SECRET to enable.",
            }

    from app.services.oauth_service import OAuthService
    svc = OAuthService(db, redis, settings)

    try:
        auth_url = await svc.generate_connect_url(platform, tenant_id, artist_id)
    except Exception as exc:
        logger.error("Failed to generate connect URL for %s: %s", platform, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return {"auth_url": auth_url}


@router.post("/{channel_id}/refresh")
async def refresh_token(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    settings: Settings = Depends(get_settings),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Force refresh tokens for a channel connection."""
    from app.services.oauth_service import OAuthService
    svc = OAuthService(db, redis, settings)

    try:
        tokens = await svc.refresh_channel_token(channel_id, tenant_id)
        return {
            "status": "refreshed",
            "expires_at": tokens.expires_at.isoformat() if tokens.expires_at else None,
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.error("Token refresh failed for channel %s: %s", channel_id, exc)
        raise HTTPException(status_code=500, detail=f"Refresh failed: {exc}")


@router.get("/{channel_id}/health")
async def channel_health(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    settings: Settings = Depends(get_settings),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Full capability health check for a channel connection."""
    from app.services.oauth_service import OAuthService
    svc = OAuthService(db, redis, settings)

    try:
        return await svc.check_health(channel_id, tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/{channel_id}/disconnect")
async def disconnect_channel(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    settings: Settings = Depends(get_settings),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Disconnect a channel — revoke upstream + delete record."""
    import logging
    logger = logging.getLogger(__name__)

    # Best-effort upstream token revocation
    try:
        from app.services.oauth_service import OAuthService
        svc = OAuthService(db, redis, settings)
        await svc.revoke_channel_token(channel_id, tenant_id)
    except Exception as exc:
        logger.warning("Upstream revocation failed (non-fatal): %s", exc)

    # Detach all child rows from channel (preserve them) then delete the channel.
    # Anything with an FK to channel_connections.id MUST be nullified here, or
    # the DELETE will fail with a constraint violation and the user will be
    # permanently unable to disconnect.
    try:
        from amplify.db.models.channel import ChannelConnectionModel
        from sqlalchemy import select, text
        # Nullify channel_id on posts (FK is ON DELETE SET NULL, but be explicit
        # so post counts are accurate the moment we commit).
        posts_result = await db.execute(
            text("UPDATE posts SET channel_id = NULL WHERE channel_id = :cid"),
            {"cid": str(channel_id)},
        )
        # Nullify channel_id on assisted_tasks (FK has no ON DELETE rule —
        # without this, any row referencing this channel would block the
        # DELETE below with a FK constraint violation).
        tasks_result = await db.execute(
            text("UPDATE assisted_tasks SET channel_id = NULL WHERE channel_id = :cid"),
            {"cid": str(channel_id)},
        )
        logger.info(
            "Disconnect channel %s: detached %d posts, %d assisted_tasks",
            channel_id, posts_result.rowcount, tasks_result.rowcount,
        )
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
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Channel delete failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Disconnect failed: {exc}")

    return {"status": "disconnected"}


# ── Public callback router (no tenant middleware) ─────────────

callback_router = APIRouter(tags=["oauth-callback"])


@callback_router.get("/oauth/callback/{platform}")
async def oauth_callback(
    platform: str,
    request: Request,
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
    error_description: str = Query(None),
):
    """Handle OAuth callback from platform. Redirects browser to frontend."""
    settings: Settings = request.app.state.settings
    frontend_url = settings.frontend_url

    if error:
        logger.warning("OAuth error for %s: %s — %s", platform, error, error_description)
        return RedirectResponse(
            url=f"{frontend_url}/channels?connected={platform}&status=error&message={error_description or error}"
        )

    if not code or not state:
        return RedirectResponse(
            url=f"{frontend_url}/channels?connected={platform}&status=error&message=missing_code_or_state"
        )

    if platform not in SUPPORTED_PLATFORMS:
        return RedirectResponse(
            url=f"{frontend_url}/channels?connected={platform}&status=error&message=unsupported_platform"
        )

    # Get a DB session from the app state
    async_session = request.app.state.async_session
    redis = request.app.state.redis

    async with async_session() as db:
        try:
            from app.services.oauth_service import OAuthService
            from sqlalchemy import text
            svc = OAuthService(db, redis, settings)
            channel = await svc.handle_callback(platform, code, state)

            # Re-associate orphaned posts (from prior disconnect) with the new channel
            posts_reassoc = await db.execute(
                text(
                    "UPDATE posts SET channel_id = :new_cid "
                    "WHERE channel_id IS NULL AND platform = :plat AND tenant_id = :tid"
                ),
                {
                    "new_cid": str(channel.id),
                    "plat": platform,
                    "tid": str(channel.tenant_id),
                },
            )
            # Re-associate orphaned assisted_tasks for this platform/tenant.
            tasks_reassoc = await db.execute(
                text(
                    "UPDATE assisted_tasks SET channel_id = :new_cid "
                    "WHERE channel_id IS NULL AND platform = :plat AND tenant_id = :tid"
                ),
                {
                    "new_cid": str(channel.id),
                    "plat": platform,
                    "tid": str(channel.tenant_id),
                },
            )
            logger.info(
                "Reconnect %s: re-associated %d posts, %d assisted_tasks to channel %s",
                platform, posts_reassoc.rowcount, tasks_reassoc.rowcount, channel.id,
            )

            await db.commit()

            return RedirectResponse(
                url=f"{frontend_url}/channels?connected={platform}&status=success&channel_id={channel.id}"
            )
        except ValueError as exc:
            logger.warning("OAuth callback validation failed: %s", exc)
            return RedirectResponse(
                url=f"{frontend_url}/channels?connected={platform}&status=error&message=invalid_state"
            )
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            logger.error("OAuth callback error for %s: %s\n%s", platform, exc, tb)
            # Include error detail in redirect for debugging
            from urllib.parse import quote
            err_msg = quote(str(exc)[:200])
            await db.rollback()
            return RedirectResponse(
                url=f"{frontend_url}/channels?connected={platform}&status=error&message={err_msg}"
            )


def _has_platform_creds(platform: str, settings: Settings) -> bool:
    if platform == "instagram":
        return bool(settings.instagram_client_id and settings.instagram_client_secret)
    elif platform == "youtube":
        return bool(settings.youtube_client_id and settings.youtube_client_secret)
    elif platform == "tiktok":
        return bool(settings.tiktok_client_key and settings.tiktok_client_secret)
    elif platform == "twitter":
        return bool(settings.twitter_client_id and settings.twitter_client_secret)
    return False

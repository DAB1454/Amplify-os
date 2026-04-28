"""Proactive token refresh — refreshes tokens expiring within 30 minutes.

Runs periodically so that publish jobs never hit an expired token.
Google refresh tokens never expire, so YouTube will always succeed
as long as the user hasn't revoked access.
"""

from __future__ import annotations

import importlib
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

logger = logging.getLogger(__name__)

# Refresh tokens that expire within this window
REFRESH_WINDOW = timedelta(minutes=30)

AUTH_MAP = {
    "youtube": ("amplify.adapters.youtube.auth", "YouTubeAuth"),
    "instagram": ("amplify.adapters.instagram.auth", "InstagramAuth"),
    "tiktok": ("amplify.adapters.tiktok.auth", "TikTokAuth"),
    "twitter": ("amplify.adapters.twitter.auth", "TwitterAuth"),
}


async def refresh_tokens(payload: dict) -> dict:
    """Scan all active channels and refresh tokens nearing expiry."""
    from amplify.db.session import get_async_session
    from amplify.db.models.channel import ChannelConnectionModel
    from amplify.adapters.crypto import TokenEncryptor
    from amplify.adapters.token_store import TokenStore, DatabaseTokenStore
    from app.config import Settings

    settings = Settings()
    refreshed = 0
    failed = 0
    skipped = 0

    encryptor = TokenEncryptor(
        primary_key=settings.token_encryption_key,
        old_keys=settings.token_encryption_old_keys,
        deployment_mode=settings.deployment_mode,
    )

    cutoff = datetime.utcnow() + REFRESH_WINDOW

    async with get_async_session(settings.database_url) as db:
        # Find active channels with tokens expiring soon
        result = await db.execute(
            select(ChannelConnectionModel).where(
                ChannelConnectionModel.is_active == True,
                ChannelConnectionModel.access_token.isnot(None),
                ChannelConnectionModel.access_token != "",
                ChannelConnectionModel.token_expires_at.isnot(None),
                ChannelConnectionModel.token_expires_at <= cutoff,
            )
        )
        channels = result.scalars().all()

        if not channels:
            return {"refreshed": 0, "failed": 0, "skipped": 0, "message": "No tokens need refresh"}

        logger.info("Found %d channel(s) with tokens expiring before %s", len(channels), cutoff.isoformat())

        for channel in channels:
            platform = channel.platform
            if platform not in AUTH_MAP:
                skipped += 1
                continue

            try:
                # Decrypt current tokens
                tokens = TokenStore.from_channel_connection(channel, encryptor)

                if not tokens.refresh_token and platform != "instagram":
                    logger.warning("No refresh token for %s channel %s — skipping", platform, channel.id)
                    skipped += 1
                    continue

                # Build auth class
                mod_path, cls_name = AUTH_MAP[platform]
                mod = importlib.import_module(mod_path)
                auth_class = getattr(mod, cls_name)

                if platform == "youtube":
                    auth = auth_class(
                        client_id=settings.youtube_client_id,
                        client_secret=settings.youtube_client_secret,
                        redirect_uri=settings.youtube_redirect_uri or "",
                    )
                elif platform == "instagram":
                    auth = auth_class(
                        client_id=settings.instagram_client_id,
                        client_secret=settings.instagram_client_secret,
                        redirect_uri=settings.instagram_redirect_uri or "",
                    )
                elif platform == "tiktok":
                    auth = auth_class(
                        client_key=settings.tiktok_client_key,
                        client_secret=settings.tiktok_client_secret,
                        redirect_uri=settings.tiktok_redirect_uri or "",
                    )
                elif platform == "twitter":
                    auth = auth_class(
                        client_id=settings.twitter_client_id,
                        client_secret=settings.twitter_client_secret,
                        redirect_uri=settings.twitter_redirect_uri or "",
                    )

                # Refresh
                if platform == "instagram":
                    new_tokens = await auth.refresh_token(tokens.access_token)
                else:
                    new_tokens = await auth.refresh_token(tokens.refresh_token)

                # Preserve refresh token if not returned
                if not new_tokens.refresh_token and tokens.refresh_token:
                    new_tokens.refresh_token = tokens.refresh_token

                # Persist
                token_store = DatabaseTokenStore(db, encryptor)
                await token_store.persist_tokens(channel.id, new_tokens)
                channel.last_refresh_at = datetime.utcnow()
                channel.last_health_status = "valid"
                await db.flush()

                refreshed += 1
                logger.info("Refreshed %s token for channel %s (expires %s)",
                            platform, channel.id, new_tokens.expires_at)

            except Exception as exc:
                failed += 1
                logger.warning("Failed to refresh %s token for channel %s: %s",
                               platform, channel.id, exc)
                # Distinguish transient errors from permanent auth failures.
                # If the token is already expired AND refresh failed, the user
                # must reconnect — mark as needs_reconnect so UI can surface it.
                err_str = str(exc).lower()
                is_permanent = any(kw in err_str for kw in [
                    "invalid_grant", "invalid_token", "token has been revoked",
                    "session has been invalidated", "password",
                    "no refresh token", "reconnect",
                ])
                if is_permanent or (channel.token_expires_at and datetime.utcnow() >= channel.token_expires_at):
                    channel.last_health_status = "needs_reconnect"
                    logger.error(
                        "Channel %s (%s) needs reconnect — refresh permanently failed: %s",
                        channel.id, platform, exc,
                    )
                    await _notify_token_expired(
                        db, channel.tenant_id, channel.id, platform,
                        channel.display_name or str(channel.id),
                    )
                else:
                    channel.last_health_status = "error"
                await db.flush()

    return {
        "refreshed": refreshed,
        "failed": failed,
        "skipped": skipped,
    }


async def _notify_token_expired(
    db,
    tenant_id,
    channel_id,
    platform: str,
    account_name: str,
) -> None:
    """Send in-app notification when a channel token permanently fails."""
    from amplify.core.services.notification_service import NotificationService

    try:
        svc = NotificationService(db, tenant_id)
        await svc.notify_tenant(
            event_type="channel.token_expired",
            title=f"{platform.title()} channel needs reconnection",
            body=f"The {platform.title()} connection for '{account_name}' has expired. "
                 f"Reconnect it on the Channels page to resume publishing.",
            severity="warning",
            url="/channels",
            entity_type="channel",
            entity_id=channel_id,
            roles=("owner", "admin"),
        )
    except Exception:
        logger.warning("Failed to send token expiry notification for channel %s", channel_id)

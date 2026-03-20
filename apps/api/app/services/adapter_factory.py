"""Adapter factory — loads channel, decrypts tokens, returns ready-to-use adapter."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.adapters.base import AuthenticationError
from amplify.adapters.crypto import TokenEncryptor
from amplify.adapters.token_store import TokenStore

from app.config import Settings

logger = logging.getLogger(__name__)

ADAPTER_MAP = {
    "instagram": "amplify.adapters.instagram.adapter.InstagramAdapter",
    "youtube": "amplify.adapters.youtube.adapter.YouTubeAdapter",
    "tiktok": "amplify.adapters.tiktok.adapter.TikTokAdapter",
}


async def get_adapter(
    db: AsyncSession,
    channel_id: uuid.UUID,
    settings: Settings,
):
    """Load a channel, decrypt its tokens, and return a configured adapter.

    Pre-publish checks: verifies channel capabilities include can_publish.
    Uses dry_run mode when deployment_mode is local.
    """
    from amplify.db.models.channel import ChannelConnectionModel

    result = await db.execute(
        select(ChannelConnectionModel).where(ChannelConnectionModel.id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise ValueError(f"Channel {channel_id} not found")

    platform = channel.platform
    if platform not in ADAPTER_MAP:
        raise ValueError(f"Unsupported adapter platform: {platform}")

    # Verify publish capability
    capabilities = channel.capabilities or {}
    if not capabilities.get("can_publish", False):
        raise AuthenticationError(
            f"Channel {channel_id} does not have publish capability. "
            "Reconnect with required scopes.",
            platform=platform,
        )

    # Decrypt tokens
    encryptor = TokenEncryptor(
        primary_key=settings.token_encryption_key,
        old_keys=settings.token_encryption_old_keys,
        deployment_mode=settings.deployment_mode,
    )
    tokens = TokenStore.from_channel_connection(channel, encryptor)

    if not tokens.access_token:
        raise AuthenticationError(
            "No access token — channel must be connected via OAuth",
            platform=platform,
        )

    # Import and instantiate the adapter
    module_path, class_name = ADAPTER_MAP[platform].rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    adapter_class = getattr(module, class_name)

    adapter = adapter_class(dry_run=settings.is_local)
    await adapter.connect({
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "token_expires_at": tokens.expires_at,
        "account_id": tokens.account_id,
    })

    return adapter

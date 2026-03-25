"""Post publishing worker job — policy check, adapter dispatch, retry, and alerts.

Payload schema:
{
    "post_id": "uuid",
    "tenant_id": "uuid",
    "platform": "instagram|tiktok|youtube",
    "channel_id": "uuid",
    "content": "caption text",
    "media_urls": ["https://..."],
    "destination_url": "https://...",
    "artist_id": "uuid",
    "release_id": "uuid",
    "campaign_id": "uuid",
    "media_type": "photo|reel|story|carousel",
    "scheduled_at": "2026-03-29T10:00:00",
    "recent_posts": [...],
    "recent_captions": [...],
    "retry_count": 0,
    "max_retries": 3,
    "dry_run": false
}
"""

from __future__ import annotations

import importlib
import logging
import uuid
from datetime import datetime

from sqlalchemy import select

logger = logging.getLogger(__name__)

BASE_DELAY = 30  # seconds
MAX_DELAY = 600  # 10 minutes

ADAPTER_MAP = {
    "instagram": "amplify.adapters.instagram.adapter.InstagramAdapter",
    "youtube": "amplify.adapters.youtube.adapter.YouTubeAdapter",
    "tiktok": "amplify.adapters.tiktok.adapter.TikTokAdapter",
}


async def publish_post(payload: dict) -> dict:
    """Publish content to a connected platform channel.

    1. Run the policy engine.
    2. Call the platform adapter.
    3. Update post in DB with result.
    4. On failure, compute retry backoff.
    5. On permanent failure, trigger alert.
    """
    post_id = payload.get("post_id", "unknown")
    tenant_id = payload.get("tenant_id", "")
    dry_run = payload.get("dry_run", False)
    retry_count = payload.get("retry_count", 0)
    max_retries = payload.get("max_retries", 3)
    # Posts from scan_scheduled were already approved — skip re-evaluation
    skip_policy = payload.get("skip_policy", False)

    policy_decision = "allow"
    platform = payload.get("platform", "")

    if not skip_policy:
        from amplify.core.policies.engine import ActionContext, create_default_engine

        # Build action context from payload
        scheduled_at = payload.get("scheduled_at")
        if isinstance(scheduled_at, str):
            try:
                scheduled_at = datetime.fromisoformat(scheduled_at)
            except ValueError:
                scheduled_at = None

        now_raw = payload.get("now")
        now_kwargs: dict = {}
        if isinstance(now_raw, str):
            try:
                now_kwargs["now"] = datetime.fromisoformat(now_raw)
            except ValueError:
                pass
        elif isinstance(now_raw, datetime):
            now_kwargs["now"] = now_raw

        ctx = ActionContext(
            action_type="publish",
            platform=platform,
            content=payload.get("content", ""),
            media_urls=payload.get("media_urls", []),
            destination_url=payload.get("destination_url", ""),
            artist_id=payload.get("artist_id", ""),
            release_id=payload.get("release_id", ""),
            campaign_id=payload.get("campaign_id", ""),
            scheduled_at=scheduled_at,
            recent_posts=payload.get("recent_posts", []),
            recent_captions=payload.get("recent_captions", []),
            **now_kwargs,
        )

        # ── Policy check ──────────────────────────────────────────────
        engine = create_default_engine()
        result = engine.evaluate(ctx)
        policy_decision = result.final_decision.value

        if result.blocked:
            logger.warning("Publish blocked by policy: %s", result.summary())
            await _update_post_status(post_id, tenant_id, "draft", last_error="Blocked by policy")
            return {
                "status": "blocked",
                "post_id": post_id,
                "published": False,
                "policy_decision": policy_decision,
                "reasons": result.blocking_reasons,
            }

        if result.needs_approval:
            logger.info("Publish requires approval: %s", result.summary())
            await _update_post_status(post_id, tenant_id, "queued")
            return {
                "status": "pending_approval",
                "post_id": post_id,
                "published": False,
                "policy_decision": policy_decision,
                "reasons": result.approval_reasons,
            }

    # ── Dry run ───────────────────────────────────────────────────
    if dry_run:
        logger.info("Dry run for post %s on %s", post_id, platform)
        return {
            "status": "dry_run",
            "post_id": post_id,
            "published": False,
            "policy_decision": policy_decision,
            "preview": {
                "platform": platform,
                "content": payload.get("content", ""),
                "media_urls": payload.get("media_urls", []),
                "destination_url": payload.get("destination_url", ""),
            },
        }

    # ── Publish via adapter ───────────────────────────────────────
    logger.info("Publishing %s post %s (skip_policy=%s)", platform, post_id, skip_policy)

    try:
        adapter_result = await _call_adapter(payload)
        logger.info("Published post %s: %s", post_id, adapter_result)

        # Update post in DB
        published_at = datetime.utcnow()
        await _update_post_status(
            post_id, tenant_id, "published",
            platform_post_id=adapter_result.get("platform_post_id"),
            permalink=adapter_result.get("permalink"),
            published_at=published_at,
        )

        return {
            "status": "ok",
            "post_id": post_id,
            "published": True,
            "policy_decision": policy_decision,
            "platform_post_id": adapter_result.get("platform_post_id"),
            "permalink": adapter_result.get("permalink"),
            "published_at": published_at.isoformat(),
        }
    except Exception as exc:
        retry_count += 1
        if retry_count >= max_retries:
            logger.error("Post %s failed permanently after %d attempts: %s", post_id, retry_count, exc)
            await _update_post_status(
                post_id, tenant_id, "failed",
                last_error=str(exc)[:1000],
                retry_count=retry_count,
            )
            return {
                "status": "failed",
                "post_id": post_id,
                "published": False,
                "error": str(exc),
                "retry_count": retry_count,
                "alert": True,
            }
        else:
            delay = _backoff_delay(retry_count)
            logger.warning(
                "Post %s attempt %d failed, retry in %ds: %s",
                post_id, retry_count, delay, exc,
            )
            await _update_post_status(
                post_id, tenant_id, "failed",
                last_error=str(exc)[:1000],
                retry_count=retry_count,
            )
            return {
                "status": "retry",
                "post_id": post_id,
                "published": False,
                "error": str(exc),
                "retry_count": retry_count,
                "retry_delay_seconds": delay,
            }


async def _call_adapter(payload: dict) -> dict:
    """Load the channel's adapter from DB, decrypt tokens, and publish.

    Returns dict with platform_post_id and permalink.
    """
    from amplify.db.session import get_async_session
    from amplify.db.models.channel import ChannelConnectionModel
    from amplify.adapters.crypto import TokenEncryptor
    from amplify.adapters.token_store import TokenStore
    from amplify.adapters.base import TokenExpiredError, RateLimitError
    from app.config import Settings

    settings = Settings()
    channel_id = payload.get("channel_id", "")
    platform = payload.get("platform", "")

    if not channel_id:
        raise ValueError(f"No channel_id in publish payload for post {payload.get('post_id')}")

    if platform not in ADAPTER_MAP:
        raise ValueError(f"Unsupported platform: {platform}")

    async with get_async_session(settings.database_url) as db:
        result = await db.execute(
            select(ChannelConnectionModel).where(
                ChannelConnectionModel.id == uuid.UUID(channel_id)
            )
        )
        channel = result.scalar_one_or_none()
        if channel is None:
            raise ValueError(f"Channel {channel_id} not found")

        # Decrypt tokens
        encryptor = TokenEncryptor(
            primary_key=settings.token_encryption_key,
            old_keys=settings.token_encryption_old_keys,
            deployment_mode=settings.deployment_mode,
        )
        tokens = TokenStore.from_channel_connection(channel, encryptor)

        if not tokens.access_token:
            raise ValueError(f"No access token for channel {channel_id} — reconnect via OAuth")

        # Import and instantiate adapter
        module_path, class_name = ADAPTER_MAP[platform].rsplit(".", 1)
        module = importlib.import_module(module_path)
        adapter_class = getattr(module, class_name)

        adapter = adapter_class(dry_run=settings.is_local)
        await adapter.connect({
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "token_expires_at": tokens.expires_at,
            "account_id": tokens.account_id,
        })

        try:
            pub_result = await adapter.publish(
                content=payload.get("content", ""),
                media_paths=payload.get("media_urls", []),
            )
        except TokenExpiredError:
            logger.warning("Token expired for channel %s — publish failed, needs refresh", channel_id)
            raise Exception(
                f"Token expired for {platform} channel. User needs to reconnect."
            )
        except RateLimitError as exc:
            raise Exception(
                f"Rate limited by {platform}. Retry after {exc.retry_after or 60}s."
            ) from exc

        return {
            "platform_post_id": pub_result.platform_post_id,
            "permalink": pub_result.url,
        }


async def _update_post_status(
    post_id: str,
    tenant_id: str,
    status: str,
    **extra: object,
) -> None:
    """Update post status and fields in the database."""
    from amplify.db.session import get_async_session
    from amplify.db.models.post import PostModel
    from app.config import Settings

    settings = Settings()

    try:
        async with get_async_session(settings.database_url) as db:
            result = await db.execute(
                select(PostModel).where(PostModel.id == uuid.UUID(post_id))
            )
            post = result.scalar_one_or_none()
            if post is None:
                logger.warning("Post %s not found for status update", post_id)
                return

            post.status = status
            for k, v in extra.items():
                if hasattr(post, k):
                    setattr(post, k, v)

            # Auto-commits when get_async_session exits
            logger.info("Updated post %s status to %s", post_id, status)
    except Exception:
        logger.exception("Failed to update post %s status", post_id)


def _backoff_delay(retry_count: int) -> int:
    """Exponential backoff: 30s, 120s, 480s, capped at 600s."""
    delay = BASE_DELAY * (2 ** (retry_count - 1))
    return min(int(delay), MAX_DELAY)

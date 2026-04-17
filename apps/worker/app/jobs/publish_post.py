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

from amplify.adapters.base import PublishError

logger = logging.getLogger(__name__)

BASE_DELAY = 30  # seconds
MAX_DELAY = 600  # 10 minutes

ADAPTER_MAP = {
    "instagram": "amplify.adapters.instagram.adapter.InstagramAdapter",
    "youtube": "amplify.adapters.youtube.adapter.YouTubeAdapter",
    "tiktok": "amplify.adapters.tiktok.adapter.TikTokAdapter",
    "twitter": "amplify.adapters.twitter.adapter.TwitterAdapter",
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
    except PublishError as exc:
        if not exc.permanent:
            raise  # let the generic Exception handler retry it
        logger.error("Post %s permanent publish failure: %s", post_id, exc)
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
    except ValueError as exc:
        logger.error("Post %s permanent failure: %s", post_id, exc)
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
                post_id, tenant_id, "scheduled",
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

        # Preemptive refresh: if token is expired or about to expire, refresh
        # now before connecting the adapter. Twitter tokens are short-lived
        # (2h) so use a wider buffer.
        from datetime import timedelta
        buffer = timedelta(minutes=15) if platform in ("twitter", "tiktok") else timedelta(minutes=5)
        if tokens.expires_at and datetime.utcnow() >= tokens.expires_at - buffer:
            logger.info("Token expired/expiring for %s channel %s — refreshing before publish", platform, channel_id)
            try:
                tokens = await _refresh_channel_token(
                    channel_id, channel.tenant_id, platform, tokens, encryptor, settings
                )
                logger.info("Preemptive refresh succeeded for channel %s", channel_id)
            except Exception as refresh_exc:
                logger.warning("Preemptive refresh failed for channel %s: %s", channel_id, refresh_exc)
                # If token is already expired, fail fast — no point trying with dead tokens
                if tokens.expires_at and datetime.utcnow() >= tokens.expires_at:
                    raise Exception(
                        f"Token expired for {platform} channel and refresh failed. User needs to reconnect."
                    ) from refresh_exc
                # Token not yet expired but close — continue and hope it still works

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
            # Auto-refresh token and retry once (same as web API)
            logger.info("Token expired for channel %s — attempting refresh and retry", channel_id)
            try:
                refreshed_tokens = await _refresh_channel_token(
                    channel_id, channel.tenant_id, platform, tokens, encryptor, settings
                )
                # Reconnect adapter with fresh tokens
                adapter = adapter_class(dry_run=settings.is_local)
                await adapter.connect({
                    "access_token": refreshed_tokens.access_token,
                    "refresh_token": refreshed_tokens.refresh_token,
                    "token_expires_at": refreshed_tokens.expires_at,
                    "account_id": refreshed_tokens.account_id,
                })
                pub_result = await adapter.publish(
                    content=payload.get("content", ""),
                    media_paths=payload.get("media_urls", []),
                )
            except Exception as refresh_exc:
                logger.warning("Token refresh failed for channel %s: %s", channel_id, refresh_exc)
                raise Exception(
                    f"Token expired for {platform} channel and refresh failed. User needs to reconnect."
                ) from refresh_exc
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


async def _refresh_channel_token(
    channel_id: str,
    tenant_id,
    platform: str,
    current_tokens,
    encryptor,
    settings,
):
    """Refresh an expired token and persist to DB. Returns new TokenSet."""
    from amplify.db.session import get_async_session
    from amplify.db.models.channel import ChannelConnectionModel
    from amplify.adapters.token_store import TokenSet, DatabaseTokenStore

    # Get the right auth class for the platform
    AUTH_MAP = {
        "youtube": ("amplify.adapters.youtube.auth", "YouTubeAuth"),
        "instagram": ("amplify.adapters.instagram.auth", "InstagramAuth"),
        "tiktok": ("amplify.adapters.tiktok.auth", "TikTokAuth"),
        "twitter": ("amplify.adapters.twitter.auth", "TwitterAuth"),
    }
    if platform not in AUTH_MAP:
        raise ValueError(f"No auth handler for platform: {platform}")

    mod_path, cls_name = AUTH_MAP[platform]
    mod = importlib.import_module(mod_path)
    auth_class = getattr(mod, cls_name)

    # Build auth with platform credentials from settings
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
        new_tokens = await auth.refresh_token(current_tokens.access_token)
    else:
        if not current_tokens.refresh_token:
            raise ValueError(f"No refresh token for {platform} — user needs to reconnect")
        new_tokens = await auth.refresh_token(current_tokens.refresh_token)

    # Preserve refresh token if not returned
    if not new_tokens.refresh_token and current_tokens.refresh_token:
        new_tokens.refresh_token = current_tokens.refresh_token

    # Persist to DB
    async with get_async_session(settings.database_url) as db:
        token_store = DatabaseTokenStore(db, encryptor)
        await token_store.persist_tokens(uuid.UUID(channel_id), new_tokens)

        # Update last_refresh_at
        from datetime import datetime
        result = await db.execute(
            select(ChannelConnectionModel).where(
                ChannelConnectionModel.id == uuid.UUID(channel_id)
            )
        )
        channel = result.scalar_one_or_none()
        if channel:
            channel.last_refresh_at = datetime.utcnow()
            await db.flush()

    logger.info("Refreshed token for %s channel %s", platform, channel_id)
    return new_tokens


def _backoff_delay(retry_count: int) -> int:
    """Exponential backoff: 30s, 120s, 480s, capped at 600s."""
    delay = BASE_DELAY * (2 ** (retry_count - 1))
    return min(int(delay), MAX_DELAY)

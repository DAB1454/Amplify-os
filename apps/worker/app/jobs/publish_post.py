"""Post publishing worker job — policy check, adapter dispatch, retry, and alerts.

Payload schema:
{
    "post_id": "uuid",
    "tenant_id": "uuid",
    "platform": "instagram|tiktok|youtube",
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

import logging
import math
from datetime import datetime

logger = logging.getLogger(__name__)

BASE_DELAY = 30  # seconds
MAX_DELAY = 600  # 10 minutes


async def publish_post(payload: dict) -> dict:
    """Publish content to a connected platform channel.

    1. Run the policy engine.
    2. Call the platform adapter (or stub).
    3. Capture permalink.
    4. On failure, compute retry backoff.
    5. On permanent failure, trigger alert.
    """
    from amplify.core.policies.engine import ActionContext, create_default_engine

    post_id = payload.get("post_id", "unknown")
    tenant_id = payload.get("tenant_id", "")
    dry_run = payload.get("dry_run", False)
    retry_count = payload.get("retry_count", 0)
    max_retries = payload.get("max_retries", 3)

    # Build action context from payload
    scheduled_at = payload.get("scheduled_at")
    if isinstance(scheduled_at, str):
        try:
            scheduled_at = datetime.fromisoformat(scheduled_at)
        except ValueError:
            scheduled_at = None

    # Allow explicit "now" override for deterministic testing
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
        platform=payload.get("platform", ""),
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

    if result.blocked:
        logger.warning("Publish blocked by policy: %s", result.summary())
        return {
            "status": "blocked",
            "post_id": post_id,
            "published": False,
            "policy_decision": result.final_decision.value,
            "reasons": result.blocking_reasons,
        }

    if result.needs_approval:
        logger.info("Publish requires approval: %s", result.summary())
        return {
            "status": "pending_approval",
            "post_id": post_id,
            "published": False,
            "policy_decision": result.final_decision.value,
            "reasons": result.approval_reasons,
        }

    # ── Dry run ───────────────────────────────────────────────────
    if dry_run:
        logger.info("Dry run for post %s on %s", post_id, ctx.platform)
        return {
            "status": "dry_run",
            "post_id": post_id,
            "published": False,
            "policy_decision": result.final_decision.value,
            "preview": {
                "platform": ctx.platform,
                "content": ctx.content,
                "media_urls": ctx.media_urls,
                "destination_url": ctx.destination_url,
            },
        }

    # ── Publish via adapter ───────────────────────────────────────
    logger.info("Policy check passed for %s publish (post %s)", ctx.platform, post_id)

    try:
        adapter_result = await _call_adapter(payload)
        logger.info("Published post %s: %s", post_id, adapter_result)
        return {
            "status": "ok",
            "post_id": post_id,
            "published": True,
            "policy_decision": result.final_decision.value,
            "platform_post_id": adapter_result.get("platform_post_id"),
            "permalink": adapter_result.get("permalink"),
            "published_at": datetime.utcnow().isoformat(),
        }
    except Exception as exc:
        retry_count += 1
        if retry_count >= max_retries:
            logger.error("Post %s failed permanently after %d attempts: %s", post_id, retry_count, exc)
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
            return {
                "status": "retry",
                "post_id": post_id,
                "published": False,
                "error": str(exc),
                "retry_count": retry_count,
                "retry_delay_seconds": delay,
            }


async def _call_adapter(payload: dict) -> dict:
    """Call the appropriate platform adapter.

    Returns dict with platform_post_id and permalink.
    """
    # TODO: wire real adapters based on platform
    # platform = payload.get("platform", "")
    # from amplify.adapters.instagram.adapter import InstagramAdapter
    # adapter = InstagramAdapter(dry_run=False)
    # await adapter.connect(credentials)
    # result = await adapter.publish(content, media_urls=media_urls, ...)
    # return {"platform_post_id": result.post_id, "permalink": result.permalink}

    platform = payload.get("platform", "unknown")
    post_id = payload.get("post_id", "stub")
    return {
        "platform_post_id": f"{platform}_{post_id}",
        "permalink": f"https://{platform}.com/p/{post_id}",
    }


def _backoff_delay(retry_count: int) -> int:
    """Exponential backoff: 30s, 120s, 480s, capped at 600s."""
    delay = BASE_DELAY * (2 ** (retry_count - 1))
    return min(int(delay), MAX_DELAY)

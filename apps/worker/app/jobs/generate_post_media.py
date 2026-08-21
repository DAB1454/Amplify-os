"""Generate media for a single post — async media pipeline.

Runs the SAME cascade as the API's generate-media endpoint (asset match ->
lyric / Replicate / standard video), but in the worker so the request returns
immediately. The API marks engagement.media_status="generating" and enqueues
this job; here we flip it to "complete" or "failed" when done. The frontend
polls GET /posts/{id} and reads engagement.media_status.

Payload:
    post_id: str (required)
    tenant_id: str (required)
    duration_seconds: int | None
    aspect_ratio: str = "9:16"
    generate_video: bool = True
    force_image_url: str | None
    force_audio_url: str | None
    generate_lyric_video: bool = False
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

logger = logging.getLogger(__name__)


async def _set_media_status(db, post_id: uuid.UUID, tenant_id: uuid.UUID, status: str, **extra) -> None:
    """Set engagement.media_status on the post in a clean transaction."""
    from amplify.db.models.post import PostModel

    r = await db.execute(
        select(PostModel).where(PostModel.id == post_id, PostModel.tenant_id == tenant_id)
    )
    post = r.scalar_one_or_none()
    if post is None:
        return
    eng = dict(post.engagement or {})
    eng["media_status"] = status
    for k, v in extra.items():
        eng[k] = v
    post.engagement = eng
    await db.flush()


async def generate_post_media(payload: dict) -> dict:
    """Worker handler: generate media for one post, tracking status in engagement."""
    from amplify.db.session import get_async_session
    from amplify.db.models.post import PostModel
    from amplify.media.orchestrator import generate_post_media_core
    from app.config import Settings

    post_id_str = payload.get("post_id")
    tenant_id_str = payload.get("tenant_id")
    if not post_id_str or not tenant_id_str:
        return {"status": "error", "error": "post_id and tenant_id required"}

    post_id = uuid.UUID(post_id_str)
    tenant_id = uuid.UUID(tenant_id_str)
    settings = Settings()

    async with get_async_session(settings.database_url) as db:
        result = await db.execute(
            select(PostModel).where(PostModel.id == post_id, PostModel.tenant_id == tenant_id)
        )
        post = result.scalar_one_or_none()
        if post is None:
            logger.warning("generate_post_media: post %s not found", post_id)
            return {"status": "error", "error": "post_not_found"}

        try:
            core = await generate_post_media_core(
                db, post, tenant_id, settings,
                duration_seconds=payload.get("duration_seconds"),
                aspect_ratio=payload.get("aspect_ratio", "9:16"),
                generate_video=payload.get("generate_video", True),
                force_image_url=payload.get("force_image_url"),
                force_audio_url=payload.get("force_audio_url"),
                generate_lyric_video=payload.get("generate_lyric_video", False),
            )
            eng = dict(post.engagement or {})
            eng["media_status"] = "complete"
            eng.pop("media_error", None)
            post.engagement = eng
            await db.commit()
            logger.info(
                "generate_post_media: post %s complete (video=%s, urls=%d)",
                post_id, core.get("video_generated"), len(core.get("media_urls") or []),
            )
            return {"status": "ok", **core}
        except Exception as exc:
            logger.exception("generate_post_media failed for post %s", post_id)
            # Roll back the poisoned transaction, then record failure cleanly so
            # the frontend stops polling and the user can retry.
            await db.rollback()
            try:
                await _set_media_status(db, post_id, tenant_id, "failed", media_error=str(exc)[:500])
                await db.commit()
            except Exception:
                logger.exception("generate_post_media: could not record failure for %s", post_id)
            return {"status": "error", "error": str(exc)}

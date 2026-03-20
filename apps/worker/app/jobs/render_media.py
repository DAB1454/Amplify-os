"""Media rendering job with retry logic.

Payload schema:
{
    "render_type": "caption_burn|waveform|lyric_card|hook_variant|teaser",
    "aspect_ratio": "9:16|1:1|16:9",
    "video_path": "...",
    "audio_path": "...",
    "artwork_path": "...",
    "captions": [{"text": "...", "start": 0, "end": 3}],
    "lyrics": "...",
    "hook_timestamps": [[0.0, 15.0], [30.0, 45.0]],
    "title": "...",
    "subtitle": "...",
    ...
}
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 30]  # seconds between retries


async def render_media(payload: dict) -> dict:
    """Render visual assets (reels, lyric videos, teasers) from a RenderSpec payload.

    Retries up to MAX_RETRIES times on transient failures (ffmpeg crashes,
    I/O errors). Returns the AssetManifest as a dict.
    """
    from amplify.media.models import RenderSpec
    from amplify.media.render_pipeline import RenderPipeline

    try:
        spec = RenderSpec(**payload)
    except Exception as e:
        logger.error("Invalid render spec: %s", e)
        return {"status": "error", "error": f"Invalid render spec: {e}", "assets_rendered": 0}

    pipeline = RenderPipeline()
    last_error: str = ""

    for attempt in range(1, MAX_RETRIES + 1):
        logger.info(
            "render_media attempt %d/%d for %s",
            attempt, MAX_RETRIES, spec.render_type,
        )
        try:
            manifest = await pipeline.render(spec)

            if manifest.success:
                logger.info(
                    "render_media succeeded: %d assets",
                    len(manifest.assets),
                )
                return {
                    "status": "ok",
                    "assets_rendered": len(manifest.assets),
                    "manifest": manifest.model_dump(mode="json"),
                }

            # Manifest returned but with errors (e.g. missing input files)
            last_error = "; ".join(manifest.errors)

            # Non-retryable errors (missing files, bad config)
            if any(
                kw in last_error
                for kw in ("not found", "required", "invalid", "Unknown render")
            ):
                logger.warning("Non-retryable error: %s", last_error)
                return {
                    "status": "error",
                    "error": last_error,
                    "assets_rendered": 0,
                }

        except Exception as e:
            last_error = str(e)
            logger.exception("render_media attempt %d failed", attempt)

        # Wait before retry (unless last attempt)
        if attempt < MAX_RETRIES:
            delay = RETRY_DELAYS[attempt - 1] if attempt - 1 < len(RETRY_DELAYS) else RETRY_DELAYS[-1]
            logger.info("Retrying in %ds...", delay)
            await asyncio.sleep(delay)

    logger.error("render_media exhausted retries: %s", last_error)
    return {
        "status": "error",
        "error": f"Failed after {MAX_RETRIES} attempts: {last_error}",
        "assets_rendered": 0,
    }

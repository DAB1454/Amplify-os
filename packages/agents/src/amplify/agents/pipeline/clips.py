"""Video clip library selection — picks the best unused clip for a post."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def _find_clip_for_post(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    release_id: uuid.UUID | None,
    track_reference: str | None = None,
    platform: str = "",
    campaign_id: uuid.UUID | None = None,
    day_number: int = 0,
    aspect_ratio: str = "9:16",
) -> str | None:
    """Find the best unused clip from the video clip library.

    Returns a clip URL matching the requested aspect ratio, or None if no
    clips are available (caller should fall through to the image+audio pipeline).
    """
    from sqlalchemy import func
    from amplify.db.models.video_clip import VideoClipModel

    base = select(VideoClipModel).where(
        VideoClipModel.tenant_id == tenant_id,
        VideoClipModel.status == "ready",
    )
    if release_id:
        base = base.where(VideoClipModel.release_id == release_id)

    # Resolve the post's track so we can prefer clips cut from that track's
    # video. Title match is case-insensitive — the canonical title and the
    # uploaded video's track can differ only in casing ("For Love of
    # Country" vs "For Love Of Country").
    track_id = None
    if track_reference:
        from amplify.db.models.track import TrackModel
        track_result = await db.execute(
            select(TrackModel.id).where(
                TrackModel.tenant_id == tenant_id,
                func.lower(TrackModel.title) == track_reference.strip().lower(),
            ).limit(1)
        )
        track_id = track_result.scalar_one_or_none()

    # Progressive broadening: prefer clips from the post's exact track, but
    # fall back to any clip in the same release rather than returning
    # nothing. A track with no clips of its own should still pull from the
    # release's video library instead of dropping to the image pipeline.
    clips = []
    if track_id is not None:
        track_q = base.where(VideoClipModel.track_id == track_id)
        track_q = track_q.order_by(VideoClipModel.energy_score.desc()).limit(50)
        clips = list((await db.execute(track_q)).scalars().all())

    if not clips:
        q = base.order_by(VideoClipModel.energy_score.desc()).limit(50)
        clips = list((await db.execute(q)).scalars().all())

    if not clips:
        return None

    def _clip_url(clip: VideoClipModel) -> str | None:
        if aspect_ratio in ("9:16", "vertical"):
            return clip.clip_url_vertical or clip.clip_url_square
        elif aspect_ratio in ("16:9", "landscape"):
            return clip.clip_url_landscape or clip.clip_url_square
        else:
            return clip.clip_url_square or clip.clip_url_vertical

    scored: list[tuple[float, VideoClipModel]] = []
    for clip in clips:
        score = clip.energy_score * 100
        score -= min(clip.uses_count * 10, 50)
        if clip.avg_engagement_score and clip.avg_engagement_score > 0:
            score += clip.avg_engagement_score * 5
        seed = hashlib.md5(
            f"{clip.id}:{campaign_id or ''}:{day_number}".encode()
        ).digest()
        jitter = (int.from_bytes(seed[:2], "little") % 20) - 10
        score += jitter
        if _clip_url(clip):
            scored.append((score, clip))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_clip = scored[0]

    best_clip.uses_count += 1
    best_clip.last_used_at = datetime.utcnow()

    url = _clip_url(best_clip)
    logger.info(
        "Clip library: selected clip %s (score=%.1f, uses=%d) for day %d",
        best_clip.id, best_score, best_clip.uses_count, day_number,
    )
    return url

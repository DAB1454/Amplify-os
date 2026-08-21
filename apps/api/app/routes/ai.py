"""AI content generation routes — wires ContentAgent and PlannerAgent to API."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.deps import get_db, get_settings, get_tenant_id, get_user_id, get_audit_service
from app.services.audit_service import AuditService

# Post media helpers were lifted to the shared media package so the worker can
# generate media too; re-imported here so this module's endpoints keep working.
from amplify.media.post_video import (  # noqa: F401
    _auto_generate_post_video,
    _smart_audio_offset,
    _find_lyric_sections,
    _extract_lyrics_for_segment,
    _generate_lyric_video_for_post,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


# ── Request / Response schemas ───────────────────────────────────


class GenerateCaptionRequest(BaseModel):
    platform: str
    artist_name: str
    release_title: str = ""
    track_title: str = ""
    key_message: str = ""
    tone: str = "authentic"
    brand_voice: str = ""
    hashtags: list[str] = Field(default_factory=list)


class CaptionVariant(BaseModel):
    variant_id: str = ""
    headline: str = ""
    body: str = ""
    hashtags: list[str] = Field(default_factory=list)
    cta: str = ""


class GenerateCaptionResponse(BaseModel):
    variants: list[CaptionVariant] = Field(default_factory=list)
    model: str = ""
    elapsed_ms: int = 0


class GeneratePlanRequest(BaseModel):
    campaign_id: str
    genre: str = ""
    channels: list[str] = Field(default_factory=list)
    track_listing: list[str] = Field(default_factory=list)
    destination_urls: dict[str, str] = Field(default_factory=dict)
    budget: float | None = None
    content_notes: str = ""  # e.g. "AI-generated music, no video footage, no personal photos"
    posts_per_day: int = 0  # 0 = let AI decide, 1-3 = target per channel per day
    focus: str = ""  # e.g. "engagement", "awareness", "conversions"
    timezone: str = "America/New_York"  # IANA timezone for scheduling
    youtube_strategy: str = "mixed"  # "shorts_only", "long_only", "mixed", "shorts_heavy"
    # Optional override for the goal distribution. If omitted, the system
    # picks a phase-aware default based on the campaign's relationship to
    # the release date. Keys must be from: awareness, follow, engage,
    # save, stream, purchase. Values are weights (0.0–1.0) that should
    # roughly sum to 1.0 — they are normalized either way.
    goal_mix: dict[str, float] | None = None


class DailyActionResponse(BaseModel):
    day: str = ""
    platform: str = ""
    action_type: str = ""
    content_brief: str = ""
    cta_destination: str = ""
    priority: str = "medium"
    track_reference: str = ""
    goal: str = ""  # awareness, follow, engage, save, stream, purchase


class GeneratePlanResponse(BaseModel):
    campaign_name: str = ""
    plan_start: str = ""
    plan_end: str = ""
    daily_actions: list[DailyActionResponse] = Field(default_factory=list)
    notes: str = ""
    calendar_items_created: int = 0
    draft_posts_created: int = 0
    model: str = ""
    elapsed_ms: int = 0
    goal_mix: dict[str, float] = Field(default_factory=dict)


# ── Helpers ──────────────────────────────────────────────────────


def _build_runner():
    """Create AgentRunner with default config."""
    from amplify.agents.runtime.agent_runner import AgentRunner
    from amplify.agents.runtime.config import AgentConfig

    config = AgentConfig()
    return AgentRunner(config=config)


# ── Routes ───────────────────────────────────────────────────────


@router.post("/generate-caption", response_model=GenerateCaptionResponse)
async def generate_caption(
    body: GenerateCaptionRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Generate AI caption variants for a post."""
    from amplify.agents.subagents.content_agent import ContentAgent

    try:
        runner = _build_runner()
        agent = ContentAgent(runner)

        result = await agent.generate_caption(
            tenant_id=tenant_id,
            user_id=user_id,
            artist_name=body.artist_name,
            platform=body.platform,
            content_type="caption",
            release_title=body.release_title,
            track_title=body.track_title,
            key_message=body.key_message,
            cta_destination="",
            tone=body.tone,
            brand_voice=body.brand_voice,
            hashtags=body.hashtags or None,
        )

        variants = []
        if result.structured and hasattr(result.structured, "variants"):
            for v in result.structured.variants:
                # Normalize hashtags — AI may return string or list
                raw_tags = getattr(v, "hashtags", []) or []
                if isinstance(raw_tags, str):
                    raw_tags = [t.strip() for t in raw_tags.replace(",", " ").split() if t.strip()]
                variants.append(CaptionVariant(
                    variant_id=v.resolved_id if hasattr(v, "resolved_id") else (getattr(v, "variant_id", "") or "A"),
                    headline=getattr(v, "headline", "") or "",
                    body=v.resolved_body if hasattr(v, "resolved_body") else (getattr(v, "body", "") or ""),
                    hashtags=raw_tags,
                    cta=getattr(v, "cta", "") or "",
                ))

        # If structured parsing succeeded but variants have empty bodies,
        # try to build body from headline + cta as fallback
        for v in variants:
            if not v.body and (v.headline or v.cta):
                parts = [p for p in [v.headline, v.cta] if p]
                v.body = "\n\n".join(parts)

        # If structured parsing failed, try to extract from raw text
        if not variants and result.text:
            variants.append(CaptionVariant(
                variant_id="A",
                body=result.text,
            ))

        try:
            await audit.log(
                action="ai.caption_generated",
                entity_type="ai_generation",
                user_id=user_id,
                changes={
                    "platform": body.platform,
                    "artist_name": body.artist_name,
                    "variants_count": len(variants),
                    "model": result.model,
                },
            )
        except Exception:
            pass

        return GenerateCaptionResponse(
            variants=variants,
            model=result.model,
            elapsed_ms=result.elapsed_ms,
        )

    except Exception as exc:
        logger.exception("Caption generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"AI generation failed: {exc}")


@router.post("/generate-plan", response_model=GeneratePlanResponse)
async def generate_plan(
    body: GeneratePlanRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
    settings_obj: Settings = Depends(get_settings),
):
    """Generate a campaign plan and create calendar items + draft posts.

    Thin wrapper around ``amplify.core.planner.service.plan_campaign``,
    which is the same code path the autonomy worker hits when it picks
    the ``plan`` action. The HTTP layer here only translates the request
    DTO into ``PlanOverrides``, calls the shared service, writes the
    human audit log, and shapes the response.
    """
    from amplify.core.planner import PlanOverrides, plan_campaign

    overrides = PlanOverrides(
        channels=body.channels,
        posts_per_day=body.posts_per_day,
        focus=body.focus,
        content_notes=body.content_notes,
        genre=body.genre,
        track_listing=body.track_listing,
        destination_urls=body.destination_urls,
        budget=body.budget,
        timezone=body.timezone or "America/New_York",
        youtube_strategy=body.youtube_strategy,
        goal_mix=body.goal_mix,
    )

    try:
        result = await plan_campaign(
            db,
            tenant_id=tenant_id,
            campaign_id=uuid.UUID(body.campaign_id),
            user_id=user_id,
            overrides=overrides,
            runner=_build_runner(),
        )
    except ValueError as exc:
        # Service raises ValueError for "campaign not found" — keep the
        # public 404 contract the route used to provide.
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        logger.exception("Plan generation failed: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"AI plan generation failed: {exc}"
        )

    try:
        await audit.log(
            action="ai.plan_generated",
            entity_type="campaign",
            entity_id=uuid.UUID(body.campaign_id),
            user_id=user_id,
            changes={
                "daily_actions": len(result.daily_actions),
                "calendar_items_created": result.calendar_items_created,
                "draft_posts_created": result.draft_posts_created,
                "model": result.model,
            },
        )
    except Exception:
        pass

    return GeneratePlanResponse(
        campaign_name=result.campaign_name,
        plan_start=result.plan_start,
        plan_end=result.plan_end,
        daily_actions=[DailyActionResponse(**a) for a in result.daily_actions],
        notes=result.notes,
        calendar_items_created=result.calendar_items_created,
        draft_posts_created=result.draft_posts_created,
        model=result.model,
        elapsed_ms=result.elapsed_ms,
        goal_mix=result.goal_mix,
    )


# ── Content Generation (AI captions + asset matching) ─────────


class GenerateContentRequest(BaseModel):
    campaign_id: str
    post_ids: list[str] | None = None  # If omitted, processes all eligible drafts


class GenerateContentResponse(BaseModel):
    pieces_generated: int = 0
    assets_attached: int = 0
    failed: int = 0
    skipped: int = 0


@router.post("/generate-content", response_model=GenerateContentResponse)
async def generate_content(
    body: GenerateContentRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Generate AI captions and attach media for draft posts in a campaign.

    Finds draft posts with brief-style content (short text placeholders from
    the planner) and replaces them with real AI-generated captions using the
    ContentAgent. Also attaches matching visual assets from the asset library.
    """
    from app.services.content_pipeline import generate_content_for_posts

    campaign_id = uuid.UUID(body.campaign_id)

    # Find eligible draft posts
    from amplify.db.models.post import PostModel
    from sqlalchemy import select

    stmt = select(PostModel).where(
        PostModel.campaign_id == campaign_id,
        PostModel.tenant_id == tenant_id,
        PostModel.status == "draft",
    )
    if body.post_ids:
        stmt = stmt.where(PostModel.id.in_([uuid.UUID(p) for p in body.post_ids]))

    result = await db.execute(stmt)
    posts = result.scalars().all()

    # Filter to brief-style posts (short content)
    BRIEF_THRESHOLD = 500
    eligible = [p for p in posts if not p.content_text or len(p.content_text or "") < BRIEF_THRESHOLD]

    if not eligible:
        return GenerateContentResponse()

    try:
        results = await generate_content_for_posts(
            db,
            post_ids=[p.id for p in eligible],
            tenant_id=tenant_id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.exception("Content generation failed: %s", exc)
        raise HTTPException(
            status_code=500, detail=f"Content generation failed: {exc}"
        )

    # Count outcomes
    generated = sum(1 for r in results.values() if r.get("caption_generated"))
    assets = sum(1 for r in results.values() if r.get("assets_attached") or r.get("clip_library_used"))
    failed = sum(1 for r in results.values() if r.get("error"))

    try:
        await audit.log(
            action="ai.content_generated",
            entity_type="campaign",
            entity_id=campaign_id,
            user_id=user_id,
            changes={
                "posts_processed": len(eligible),
                "captions_generated": generated,
                "assets_attached": assets,
                "failed": failed,
            },
        )
    except Exception:
        pass

    return GenerateContentResponse(
        pieces_generated=generated,
        assets_attached=assets,
        failed=failed,
        skipped=len(posts) - len(eligible),
    )


# ── Cross-track caption repair ───────────────────────────────────


class RepairCrossTrackRequest(BaseModel):
    campaign_id: str
    dry_run: bool = False  # When true, just report offenders without rewriting
    # Cap the number of posts repaired in a single request so the LLM
    # roundtrips fit inside the gateway timeout. The UI calls this
    # endpoint in a loop until the response reports
    # remaining_offenders==0. Default is conservative because each
    # repair triggers up to 3 validator-retry LLM calls (~20s each).
    limit: int = 5


class RepairCrossTrackResponse(BaseModel):
    scanned: int = 0
    offenders: int = 0
    repaired: int = 0
    repair_failed: int = 0
    # How many offenders remain unrepaired after this batch — the UI
    # uses this to decide whether to make another chunk call.
    remaining_offenders: int = 0
    details: list[dict] = Field(default_factory=list)


@router.post("/repair-cross-track-captions", response_model=RepairCrossTrackResponse)
async def repair_cross_track_captions(
    body: RepairCrossTrackRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Find posts whose caption mentions a track other than the one
    they're anchored to, and regenerate the caption via the validator.

    Built to clean up posts produced before the planner's track-swap
    fix landed — those briefs still carry the original LLM's wrong
    track name in the body while track_id / track_reference point
    elsewhere. This endpoint sweeps a campaign and fixes the drift.

    The repair walks any post status except FAILED/PUBLISHED (you
    can't retroactively edit a published post on the platform). The
    post's status, scheduled_at, and channel are unchanged — only
    content_text is rewritten. dry_run=true reports the offenders
    without touching content.
    """
    from amplify.db.models.post import PostModel
    from amplify.db.models.track import TrackModel
    from amplify.db.models.campaign import CampaignModel
    from app.services.content_pipeline import (
        generate_content_for_post,
        _caption_mentions_other_tracks,
    )
    from sqlalchemy import select

    campaign_id = uuid.UUID(body.campaign_id)

    # Resolve the campaign's release so we can load sibling tracks.
    camp_row = await db.execute(
        select(CampaignModel).where(
            CampaignModel.id == campaign_id,
            CampaignModel.tenant_id == tenant_id,
        )
    )
    campaign = camp_row.scalar_one_or_none()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if not campaign.release_id:
        # Without a release we have no notion of sibling tracks — nothing
        # to compare against, so by definition nothing to repair.
        return RepairCrossTrackResponse()

    # Build a {title_lower: TrackModel} map for the release once.
    tracks_row = await db.execute(
        select(TrackModel).where(
            TrackModel.release_id == campaign.release_id,
            TrackModel.tenant_id == tenant_id,
        )
    )
    release_tracks = list(tracks_row.scalars().all())
    if not release_tracks:
        return RepairCrossTrackResponse()
    track_by_id = {t.id: t for t in release_tracks}
    all_titles = [t.title for t in release_tracks if t.title]

    # Pull every post in the campaign that's still editable (anything
    # not published and not permanently failed).
    posts_row = await db.execute(
        select(PostModel).where(
            PostModel.campaign_id == campaign_id,
            PostModel.tenant_id == tenant_id,
            PostModel.status.notin_(["published", "failed"]),
        )
    )
    posts = list(posts_row.scalars().all())

    details: list[dict] = []
    repaired = 0
    repair_failed = 0
    offender_count = 0
    remaining_offenders = 0
    # Chunking: scan ALL posts to surface the true offender_count and
    # remaining_offenders so the UI knows whether more chunks are
    # needed, but only PROCESS up to body.limit per request.
    batch_limit = max(1, int(body.limit))
    processed_in_batch = 0
    for post in posts:
        # Anchored track title — track_id if set, else fall back to the
        # fuzzy track_reference string.
        anchored_title = ""
        if post.track_id and post.track_id in track_by_id:
            anchored_title = track_by_id[post.track_id].title or ""
        anchored_title = anchored_title or (post.track_reference or "")
        if not anchored_title:
            continue  # Nothing to validate against on this post.

        # Siblings are every release track that isn't the anchor.
        siblings = [
            t for t in all_titles
            if t.strip().lower() != anchored_title.strip().lower()
        ]
        offenders = _caption_mentions_other_tracks(
            post.content_text or "", anchored_title, siblings,
        )
        if not offenders:
            continue

        offender_count += 1

        # If this offender is past our per-batch cap, just count it as
        # remaining so the UI knows to call again. Don't run the LLM.
        if not body.dry_run and processed_in_batch >= batch_limit:
            remaining_offenders += 1
            continue

        detail: dict = {
            "post_id": str(post.id),
            "platform": post.platform,
            "anchor": anchored_title,
            "mentions": offenders,
        }

        if body.dry_run:
            detail["action"] = "dry_run"
            details.append(detail)
            continue

        processed_in_batch += 1
        try:
            # generate_content_for_post regenerates via the validator,
            # which retries until the caption no longer references
            # sibling tracks (or surfaces validator_meta if it can't).
            result = await generate_content_for_post(
                db, post.id, tenant_id, user_id,
            )
            if result.get("caption_generated"):
                repaired += 1
                detail["action"] = "repaired"
                detail["validator"] = result.get("caption_validator")
            else:
                repair_failed += 1
                detail["action"] = "repair_failed"
                detail["reason"] = result.get("caption_error") or "no_caption_generated"
        except Exception as exc:
            repair_failed += 1
            detail["action"] = "repair_failed"
            detail["reason"] = str(exc)[:300]
            logger.warning("Repair failed for post %s: %s", post.id, exc)
        details.append(detail)

    try:
        await audit.log(
            action="ai.cross_track_repair",
            entity_type="campaign",
            entity_id=campaign_id,
            user_id=user_id,
            changes={
                "scanned": len(posts),
                "offenders": offender_count,
                "repaired": repaired,
                "repair_failed": repair_failed,
                "dry_run": body.dry_run,
            },
        )
    except Exception:
        pass

    return RepairCrossTrackResponse(
        scanned=len(posts),
        offenders=offender_count,
        repaired=repaired,
        repair_failed=repair_failed,
        remaining_offenders=remaining_offenders,
        details=details,
    )


# ── Static Video (Tier 1) Generation ──────────────────────────


class GenerateStaticVideoRequest(BaseModel):
    post_id: str
    duration_seconds: int = 15
    aspect_ratio: str = "9:16"


class GenerateStaticVideoResponse(BaseModel):
    video_url: str = ""
    asset_id: str | None = None
    elapsed_ms: int = 0


from amplify.agents.pipeline.clips import _find_clip_for_post  # noqa: E402,F401


@router.post("/generate-static-video", response_model=GenerateStaticVideoResponse)
async def generate_static_video_endpoint(
    body: GenerateStaticVideoRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    settings_obj: Settings = Depends(get_settings),
):
    """Generate a static image+audio video for a post (Tier 1).

    Picks the best image and audio from the asset library,
    combines into a short video clip via FFmpeg, and attaches to the post.
    """
    import time
    from sqlalchemy import select
    from amplify.db.models.post import PostModel
    from amplify.db.models.campaign import CampaignModel

    start_time = time.time()

    # Load the post
    post_result = await db.execute(
        select(PostModel).where(PostModel.id == uuid.UUID(body.post_id), PostModel.tenant_id == tenant_id)
    )
    post = post_result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # Resolve artist/release from campaign
    artist_id = None
    release_id = None
    if post.campaign_id:
        camp_result = await db.execute(select(CampaignModel).where(CampaignModel.id == post.campaign_id))
        campaign = camp_result.scalar_one_or_none()
        if campaign:
            artist_id = campaign.artist_id
            release_id = campaign.release_id

    # Resolve the canonical track title from the hard track_id anchor when
    # present. Falls back to the fuzzy track_reference for legacy posts.
    canonical_track_title = post.track_reference or ""
    if post.track_id:
        from amplify.db.models.track import TrackModel
        _tr = await db.execute(
            select(TrackModel).where(
                TrackModel.id == post.track_id,
                TrackModel.tenant_id == tenant_id,
            )
        )
        _anchored = _tr.scalar_one_or_none()
        if _anchored:
            canonical_track_title = _anchored.title or canonical_track_title

    # ── Check clip library first ────────────────────────────────
    # If there are pre-extracted clips from a music video, use one
    # instead of generating a static image+audio video.
    clip_url = await _find_clip_for_post(
        db=db,
        tenant_id=tenant_id,
        release_id=release_id,
        track_reference=canonical_track_title,
        platform=post.platform,
        campaign_id=post.campaign_id,
        day_number=post.day_number or 1,
        aspect_ratio=body.aspect_ratio,
    )
    if clip_url:
        post.media_urls = [clip_url]
        await db.flush()
        elapsed = int((time.time() - start_time) * 1000)
        logger.info("Used clip library for post %s in %dms", body.post_id, elapsed)
        return GenerateStaticVideoResponse(video_url=clip_url, elapsed_ms=elapsed)

    # Find an image — use existing post media or find from library
    from app.services.content_pipeline import _find_matching_assets
    image_urls = await _find_matching_assets(
        db=db,
        tenant_id=tenant_id,
        artist_id=artist_id,
        release_id=release_id,
        campaign_id=post.campaign_id,
        platform=post.platform,
        action_type=post.action_type_label,
        content_hint=post.content_text or "",
        track_reference=canonical_track_title,
        day_number=post.day_number,
        max_results=1,
    )
    if not image_urls:
        raise HTTPException(status_code=400, detail="No images in asset library. Upload album art or promo photos first.")

    # Generate the video
    video_url = await _auto_generate_post_video(
        db=db,
        tenant_id=tenant_id,
        image_url=image_urls[0],
        artist_id=artist_id,
        release_id=release_id,
        content_hint=post.content_text or "",
        day_number=post.day_number or 1,
        settings=settings_obj,
        duration=min(max(body.duration_seconds, 10), 60),
        track_id=post.track_id,
    )

    if not video_url:
        raise HTTPException(status_code=400, detail="No audio tracks in asset library. Upload audio files first.")

    # Replace media on the post
    post.media_urls = [video_url]
    await db.flush()

    elapsed = int((time.time() - start_time) * 1000)
    logger.info("Static video generated for post %s in %dms", body.post_id, elapsed)

    return GenerateStaticVideoResponse(
        video_url=video_url,
        elapsed_ms=elapsed,
    )


# ── Lyric Video (Tier 2) Generation ──────────────────────────


class GenerateLyricVideoRequest(BaseModel):
    post_id: str | None = None  # Auto-attach result to this post
    image_url: str = ""  # S3 URL of background image
    audio_url: str = ""  # S3 URL of audio
    lyrics: str = ""  # Raw lyrics text
    aspect_ratio: str = "9:16"  # 9:16, 1:1, 16:9
    duration_seconds: int = 30  # 15, 30, or 60
    audio_start_seconds: int = 0  # Where to start audio (0 = auto-pick)
    artist_name: str = ""
    track_title: str = ""
    # Convenience: auto-resolve from track/release
    track_id: str | None = None
    release_id: str | None = None


class GenerateLyricVideoResponse(BaseModel):
    video_url: str = ""
    duration_seconds: int = 0
    aspect_ratio: str = ""
    asset_id: str | None = None
    elapsed_ms: int = 0


@router.post("/generate-lyric-video", response_model=GenerateLyricVideoResponse)
async def generate_lyric_video(
    body: GenerateLyricVideoRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Generate a lyric video from image + audio + lyrics using FFmpeg."""
    import time
    import tempfile
    from pathlib import Path
    from sqlalchemy import select
    start_time = time.time()

    image_url = body.image_url
    audio_url = body.audio_url
    lyrics = body.lyrics
    artist_name = body.artist_name
    track_title = body.track_title

    # Auto-resolve from track if track_id provided
    if body.track_id:
        from amplify.db.models.track import TrackModel
        track_result = await db.execute(
            select(TrackModel).where(TrackModel.id == uuid.UUID(body.track_id))
        )
        track = track_result.scalar_one_or_none()
        if track:
            if not lyrics and track.lyrics:
                lyrics = track.lyrics
            if not audio_url and track.audio_url:
                audio_url = track.audio_url
            if not track_title and track.title:
                track_title = track.title

    # If caller passed an explicit audio_url, look up the track that owns
    # it so the lyrics we display match the audio we play. Without this,
    # lyrics-by-caption-match can pull lyrics from an unrelated track.
    if audio_url and not lyrics:
        from amplify.db.models.track import TrackModel as _TrackByUrl
        url_track_r = await db.execute(
            select(_TrackByUrl).where(
                _TrackByUrl.tenant_id == tenant_id,
                _TrackByUrl.audio_url == audio_url,
            ).limit(1)
        )
        url_track = url_track_r.scalar_one_or_none()
        if url_track:
            if url_track.lyrics:
                lyrics = url_track.lyrics
            if not track_title and url_track.title:
                track_title = url_track.title
            logger.info(
                "Lyric video endpoint: matched track '%s' by audio_url (lyrics=%s chars)",
                url_track.title, len(lyrics),
            )

    # Auto-resolve track from post content or release tracks when no track_id
    if not body.track_id and body.post_id:
        from amplify.db.models.post import PostModel
        from amplify.db.models.track import TrackModel as _TrackModel
        post_result = await db.execute(
            select(PostModel).where(PostModel.id == uuid.UUID(body.post_id))
        )
        post = post_result.scalar_one_or_none()
        post_text_raw = (post.content_text or "") if post else ""

        # Find release_id from post's campaign
        resolve_release_id = body.release_id
        if not resolve_release_id and post and post.campaign_id:
            from amplify.db.models.campaign import CampaignModel
            camp_result = await db.execute(
                select(CampaignModel).where(CampaignModel.id == post.campaign_id)
            )
            camp = camp_result.scalar_one_or_none()
            if camp and camp.release_id:
                resolve_release_id = str(camp.release_id)

        if resolve_release_id:
            tracks_result = await db.execute(
                select(_TrackModel).where(
                    _TrackModel.release_id == uuid.UUID(resolve_release_id)
                ).order_by(_TrackModel.track_number)
            )
            all_tracks = list(tracks_result.scalars().all())

            if all_tracks and post_text_raw:
                # Match track name to post content
                from app.services.content_pipeline import (
                    _normalize_for_match, _any_phrase_match, _extract_track_reference,
                )
                # Normalize before lowering so camelCase hashtags get split
                p_text = _normalize_for_match(post_text_raw).lower()
                explicit_ref = _extract_track_reference(post_text_raw)
                explicit_clean = _normalize_for_match(explicit_ref).lower() if explicit_ref else ""

                best_track = None
                best_score = 0
                for t in all_tracks:
                    t_name = _normalize_for_match((t.title or "").lower())
                    score = 0
                    # Priority 1: Explicit "Featuring: X" match
                    if explicit_clean and t_name and (t_name in explicit_clean or explicit_clean in t_name):
                        score = 80
                    elif t_name and len(t_name) > 3 and t_name in p_text:
                        score = 50
                    elif _any_phrase_match(p_text, (t.title or "").lower()):
                        score = 40
                    if score > best_score:
                        best_track, best_score = t, score

                if best_track:
                    if not lyrics and best_track.lyrics:
                        lyrics = best_track.lyrics
                    if not audio_url and best_track.audio_url:
                        audio_url = best_track.audio_url
                    if not track_title and best_track.title:
                        track_title = best_track.title
                    logger.info("Auto-matched track '%s' (score=%d) for lyric video from post content", best_track.title, best_score)

    # Auto-resolve image from release artwork
    if body.release_id and not image_url:
        from amplify.db.models.release import ReleaseModel
        release_result = await db.execute(
            select(ReleaseModel).where(ReleaseModel.id == uuid.UUID(body.release_id))
        )
        release = release_result.scalar_one_or_none()
        if release and release.artwork_url:
            image_url = release.artwork_url

    # Fall back: find an image asset from the library
    if not image_url:
        from amplify.db.models.asset import AssetModel
        asset_result = await db.execute(
            select(AssetModel).where(
                AssetModel.tenant_id == tenant_id,
                AssetModel.asset_type.in_(["image", "album_art", "promo_photo"]),
            ).order_by(AssetModel.created_at.desc()).limit(1)
        )
        asset = asset_result.scalar_one_or_none()
        if asset:
            image_url = asset.file_url

    # Fall back: find audio from asset library matched to post content
    if not audio_url:
        from amplify.db.models.asset import AssetModel as _AssetModel
        from sqlalchemy import or_ as _or
        audio_q = select(_AssetModel).where(
            _AssetModel.tenant_id == tenant_id,
            _AssetModel.asset_type == "audio",
            _or(_AssetModel.approval_status != "rejected", _AssetModel.approval_status.is_(None)),
        ).order_by(_AssetModel.created_at.desc()).limit(20)
        audio_result = await db.execute(audio_q)
        audio_assets = list(audio_result.scalars().all())
        if audio_assets:
            # Content-match: pick the track mentioned in the post/lyrics
            from app.services.content_pipeline import _normalize_for_match, _any_phrase_match
            hint = (lyrics or track_title or "").lower()
            best, best_score = audio_assets[0], 0
            for a in audio_assets:
                n = _normalize_for_match((a.name or "").lower())
                h = _normalize_for_match(hint)
                s = 50 if (n and len(n) > 3 and n in h) else (40 if _any_phrase_match(hint, (a.name or "").lower()) else 0)
                if s > best_score:
                    best, best_score = a, s
            audio_url = best.file_url

    # Validate inputs
    if not image_url:
        raise HTTPException(status_code=400, detail="No image available. Upload album art or provide an image URL.")
    if not audio_url:
        raise HTTPException(status_code=400, detail="No audio available. Upload a track or provide an audio URL.")
    if not lyrics:
        raise HTTPException(status_code=400, detail="No lyrics available. Add lyrics to the track first.")

    # Validate duration
    duration = min(max(body.duration_seconds, 10), 90)

    # Pick an audio offset and extract only the corresponding lyrics
    audio_start = body.audio_start_seconds or 0
    if not audio_start and body.post_id:
        # Auto-pick using smart offset
        try:
            audio_start = await _smart_audio_offset(
                db=db, tenant_id=tenant_id,
                release_id=uuid.UUID(body.release_id) if body.release_id else None,
                audio_name=track_title or "",
                day_number=1, duration=duration,
            )
        except Exception:
            audio_start = 0

    # Extract only the lyrics for the audio segment being played
    segment_lyrics = _extract_lyrics_for_segment(
        lyrics, audio_start, duration,
        track_duration=None,  # Direct endpoint doesn't always have track_duration
    )

    try:
        from app.services.video_generator import generate_lyric_video as gen_video, download_url_to_file

        with tempfile.TemporaryDirectory(prefix="lyricvid_") as tmp_dir:
            # Determine file extensions from URLs
            img_ext = Path(image_url.split("?")[0]).suffix or ".jpg"
            aud_ext = Path(audio_url.split("?")[0]).suffix or ".mp3"

            img_path = str(Path(tmp_dir) / f"input{img_ext}")
            aud_path = str(Path(tmp_dir) / f"input{aud_ext}")
            out_path = str(Path(tmp_dir) / "output.mp4")

            # Download inputs
            await download_url_to_file(image_url, img_path)
            await download_url_to_file(audio_url, aud_path)

            # Generate video
            await gen_video(
                image_path=img_path,
                audio_path=aud_path,
                lyrics=segment_lyrics,
                output_path=out_path,
                aspect_ratio=body.aspect_ratio,
                duration_seconds=duration,
                audio_start_seconds=audio_start,
                artist_name=artist_name,
                track_title=track_title,
            )

            # Upload to S3
            from app.services.media_service import MediaService
            from app.config import Settings
            settings = Settings()
            media_svc = MediaService(
                s3_bucket=settings.s3_bucket,
                s3_region=settings.s3_region,
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
                media_base_url=settings.media_base_url,
            )
            with open(out_path, "rb") as f:
                video_url = await media_svc.upload(
                    tenant_id, f, f"lyric-video-{uuid.uuid4()}.mp4", "video/mp4"
                )

        # Resolve artist/release/campaign from the post for asset linking
        asset_artist_id = None
        asset_release_id = None
        asset_campaign_id = None
        if body.post_id:
            from amplify.db.models.post import PostModel
            from amplify.db.models.campaign import CampaignModel
            post_q = await db.execute(
                select(PostModel).where(PostModel.id == uuid.UUID(body.post_id))
            )
            linked_post = post_q.scalar_one_or_none()
            if linked_post and linked_post.campaign_id:
                asset_campaign_id = linked_post.campaign_id
                camp_q = await db.execute(
                    select(CampaignModel).where(CampaignModel.id == linked_post.campaign_id)
                )
                linked_camp = camp_q.scalar_one_or_none()
                if linked_camp:
                    asset_artist_id = linked_camp.artist_id
                    asset_release_id = linked_camp.release_id
                    if not artist_name and linked_camp.artist_id:
                        from amplify.db.models.artist import ArtistModel
                        art_q = await db.execute(
                            select(ArtistModel).where(ArtistModel.id == linked_camp.artist_id)
                        )
                        art = art_q.scalar_one_or_none()
                        if art:
                            artist_name = art.name

        # Create asset record
        from amplify.db.models.asset import AssetModel
        asset = AssetModel(
            tenant_id=tenant_id,
            artist_id=asset_artist_id,
            release_id=asset_release_id,
            campaign_id=asset_campaign_id,
            asset_type="lyric_video",
            name=f"Lyric Video — {track_title or 'Untitled'}",
            description=f"Generated lyric video for {artist_name} — {track_title}",
            file_url=video_url,
            mime_type="video/mp4",
            tags=["lyric_video", "generated"],
            source="ai_generated",
        )
        db.add(asset)
        await db.flush()
        await db.refresh(asset)

        # Auto-attach to post — replaces existing media (upgrade from static image/video)
        if body.post_id:
            from amplify.db.models.post import PostModel
            post_result = await db.execute(
                select(PostModel).where(
                    PostModel.id == uuid.UUID(body.post_id),
                    PostModel.tenant_id == tenant_id,
                )
            )
            post = post_result.scalar_one_or_none()
            if post:
                post.media_urls = [video_url]
                # Persist the source audio URL on the post so a future
                # regeneration via the draft Lyric Video button can pick
                # the same track instead of caption-matching to a random
                # one. media_urls is overwritten with the rendered video,
                # so we stash the original audio in engagement instead.
                if audio_url:
                    eng = dict(post.engagement or {})
                    eng["source_audio_url"] = audio_url
                    post.engagement = eng
                await db.flush()
                logger.info("Replaced media on post %s with lyric video", body.post_id)

        elapsed = int((time.time() - start_time) * 1000)

        try:
            await audit.log(
                action="ai.lyric_video_generated",
                entity_type="asset",
                entity_id=asset.id,
                user_id=user_id,
                changes={
                    "track_title": track_title,
                    "duration": duration,
                    "aspect_ratio": body.aspect_ratio,
                },
            )
        except Exception:
            pass

        return GenerateLyricVideoResponse(
            video_url=video_url,
            duration_seconds=duration,
            aspect_ratio=body.aspect_ratio,
            asset_id=str(asset.id),
            elapsed_ms=elapsed,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Lyric video generation failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Video generation failed: {exc}")


# ── AI Video Generation (Replicate) ────────────────────────────


class GenerateAIVideoRequest(BaseModel):
    prompt: str = ""  # Scene description or let AI generate from lyrics
    lyrics: str = ""  # Auto-generate scene prompts from lyrics
    image_url: str = ""  # Starting image (album art)
    audio_url: str = ""  # Audio to sync with
    audio_start: float | None = None  # Start offset in seconds (e.g. 14 for 0:14)
    audio_end: float | None = None  # End offset in seconds (e.g. 44 for 0:44)
    aspect_ratio: str = "9:16"
    duration_seconds: int = 30
    num_scenes: int = 6  # Number of AI-generated clips
    artist_name: str = ""
    track_title: str = ""
    track_id: str | None = None
    release_id: str | None = None
    post_id: str | None = None


class GenerateAIVideoResponse(BaseModel):
    status: str = "generating"  # "generating" (accepted) or "complete"
    video_url: str = ""
    asset_id: str | None = None
    approval_status: str = "pending_review"
    estimated_cost: float = 0.0
    clips_generated: int = 0
    elapsed_ms: int = 0


class EstimateAIVideoCostRequest(BaseModel):
    num_scenes: int = 6
    duration_seconds: int = 30


class EstimateAIVideoCostResponse(BaseModel):
    estimated_cost: float = 0.0
    num_clips: int = 0
    cost_per_clip: float = 0.0


@router.post("/estimate-video-cost", response_model=EstimateAIVideoCostResponse)
async def estimate_video_cost(body: EstimateAIVideoCostRequest):
    """Estimate the cost of AI video generation before committing."""
    from app.services.replicate_video import ESTIMATED_COST_PER_CLIP
    return EstimateAIVideoCostResponse(
        estimated_cost=body.num_scenes * ESTIMATED_COST_PER_CLIP,
        num_clips=body.num_scenes,
        cost_per_clip=ESTIMATED_COST_PER_CLIP,
    )


@router.post("/generate-ai-video", response_model=GenerateAIVideoResponse)
async def generate_ai_video(
    request: Request,
    body: GenerateAIVideoRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Generate AI video clips from prompts, stitch with audio, save to library.

    Kicks off generation in the background and returns immediately.
    The post's engagement.ai_video_status field tracks progress:
      "generating" → "complete" or "failed"
    Frontend should poll GET /posts/{id} to check status.
    """
    from sqlalchemy import select
    from app.config import Settings

    settings = Settings()

    if not settings.replicate_api_token:
        raise HTTPException(status_code=503, detail="AI video generation not configured. Set REPLICATE_API_TOKEN.")

    # Persist the user's prompt immediately so it's never lost
    if body.post_id and body.prompt:
        from amplify.db.models.post import PostModel as _PromptPost
        _pp_r = await db.execute(
            select(_PromptPost).where(
                _PromptPost.id == uuid.UUID(body.post_id),
                _PromptPost.tenant_id == tenant_id,
            )
        )
        _pp = _pp_r.scalar_one_or_none()
        if _pp:
            eng = dict(_pp.engagement or {})
            eng["ai_video_prompt"] = body.prompt
            eng["ai_video_settings"] = {
                "num_scenes": body.num_scenes,
                "duration_seconds": body.duration_seconds,
                "aspect_ratio": body.aspect_ratio,
            }
            _pp.engagement = eng
            await db.flush()

    # Resolve inputs from track/release if needed
    image_url = body.image_url
    audio_url = body.audio_url
    lyrics = body.lyrics
    artist_name = body.artist_name
    track_title = body.track_title

    if body.track_id:
        from amplify.db.models.track import TrackModel
        track_result = await db.execute(
            select(TrackModel).where(TrackModel.id == uuid.UUID(body.track_id))
        )
        track = track_result.scalar_one_or_none()
        if track:
            if not lyrics and track.lyrics:
                lyrics = track.lyrics
            if not audio_url and track.audio_url:
                audio_url = track.audio_url
            if not track_title and track.title:
                track_title = track.title

    # Fall back: check post's own media_urls and engagement for image/audio
    if body.post_id and (not image_url or not audio_url):
        from amplify.db.models.post import PostModel as _PostModel
        _post_q = await db.execute(
            select(_PostModel).where(
                _PostModel.id == uuid.UUID(body.post_id),
                _PostModel.tenant_id == tenant_id,
            )
        )
        _linked = _post_q.scalar_one_or_none()
        if _linked:
            # Check engagement for previously used audio (from prior AI video generation)
            _eng = _linked.engagement or {}
            if not audio_url and _eng.get("ai_video_audio_url"):
                audio_url = _eng["ai_video_audio_url"]
            if _linked.media_urls:
                # Same extension-detection bug we fixed in posts.py:425 — strip
                # query/fragment first so signed URLs aren't misclassified, and
                # fall back to the assets table for URLs without an extension.
                _img_exts = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")
                _aud_exts = (".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a")

                def _ext(url: str) -> str:
                    base = url.split("?", 1)[0].split("#", 1)[0].lower()
                    dot = base.rfind(".")
                    return base[dot:] if dot >= 0 else ""

                _unknown: list[str] = []
                for mu in _linked.media_urls:
                    e = _ext(mu)
                    if not image_url and e in _img_exts:
                        image_url = mu
                    elif not audio_url and e in _aud_exts:
                        audio_url = mu
                    elif e not in _img_exts and e not in _aud_exts:
                        _unknown.append(mu)

                if _unknown and (not image_url or not audio_url):
                    from amplify.db.models.asset import AssetModel
                    _ar = await db.execute(
                        select(AssetModel.file_url, AssetModel.asset_type).where(
                            AssetModel.tenant_id == tenant_id,
                            AssetModel.file_url.in_(_unknown),
                        )
                    )
                    _type_by_url = {row.file_url: (row.asset_type or "").lower() for row in _ar}
                    for mu in _unknown:
                        atype = _type_by_url.get(mu, "")
                        if not image_url and atype in ("image", "album_art", "promo_photo", "logo"):
                            image_url = mu
                        elif not audio_url and atype == "audio":
                            audio_url = mu

    if body.release_id and not image_url:
        from amplify.db.models.release import ReleaseModel
        release_result = await db.execute(
            select(ReleaseModel).where(ReleaseModel.id == uuid.UUID(body.release_id))
        )
        release = release_result.scalar_one_or_none()
        if release and release.artwork_url:
            image_url = release.artwork_url

    # Fall back to asset library image
    if not image_url:
        from amplify.db.models.asset import AssetModel
        asset_result = await db.execute(
            select(AssetModel).where(
                AssetModel.tenant_id == tenant_id,
                AssetModel.asset_type.in_(["image", "album_art", "promo_photo"]),
            ).order_by(AssetModel.created_at.desc()).limit(1)
        )
        asset_img = asset_result.scalar_one_or_none()
        if asset_img:
            image_url = asset_img.file_url

    # Fall back: find audio from asset library matched to post content
    if not audio_url:
        from amplify.db.models.asset import AssetModel as _AssetModel2
        from sqlalchemy import or_ as _or2
        audio_q2 = select(_AssetModel2).where(
            _AssetModel2.tenant_id == tenant_id,
            _AssetModel2.asset_type == "audio",
            _or2(_AssetModel2.approval_status != "rejected", _AssetModel2.approval_status.is_(None)),
        ).order_by(_AssetModel2.created_at.desc()).limit(20)
        audio_result2 = await db.execute(audio_q2)
        audio_assets2 = list(audio_result2.scalars().all())
        if audio_assets2:
            from app.services.content_pipeline import _normalize_for_match, _any_phrase_match
            hint2 = (lyrics or body.prompt or track_title or "").lower()
            best2, best_score2 = audio_assets2[0], 0
            for a2 in audio_assets2:
                n2 = _normalize_for_match((a2.name or "").lower())
                h2 = _normalize_for_match(hint2)
                s2 = 50 if (n2 and len(n2) > 3 and n2 in h2) else (40 if _any_phrase_match(hint2, (a2.name or "").lower()) else 0)
                if s2 > best_score2:
                    best2, best_score2 = a2, s2
            audio_url = best2.file_url

    if not audio_url:
        raise HTTPException(status_code=400, detail="No audio available. Upload a track or provide an audio URL.")

    # Generate scene prompts from lyrics if no explicit prompt
    from app.services.replicate_video import (
        generate_scene_prompts_from_lyrics,
        generate_music_video,
        parse_timed_scenes,
        ESTIMATED_COST_PER_CLIP,
    )

    scene_durations: list[float] | None = None
    audio_start = body.audio_start
    audio_end = body.audio_end

    if body.prompt:
        # Try to parse timed shots from the prompt (e.g., "Shot #1 (0:14-0:21): ...")
        timed_scenes = parse_timed_scenes(body.prompt)
        if timed_scenes:
            prompts = [s["prompt"] for s in timed_scenes]
            scene_durations = [s["duration"] for s in timed_scenes]
            # Auto-detect audio range from shot timestamps if not explicitly set
            if audio_start is None:
                audio_start = timed_scenes[0]["start"]
            if audio_end is None:
                audio_end = timed_scenes[-1]["end"]
            logger.info(
                "Parsed %d timed scenes (audio %.1f-%.1fs, durations=%s)",
                len(prompts), audio_start, audio_end, scene_durations,
            )
        else:
            # Single prompt — replicate it for each scene
            prompts = [body.prompt] * body.num_scenes
    elif lyrics:
        prompts = await generate_scene_prompts_from_lyrics(
            lyrics=lyrics,
            artist_name=artist_name,
            track_title=track_title,
            num_scenes=body.num_scenes,
        )
    else:
        prompts = [
            f"Cinematic music video scene for {artist_name} - {track_title}. "
            f"Beautiful cinematography, atmospheric lighting."
        ] * body.num_scenes

    # Calculate total duration from audio range or explicit setting
    if audio_start is not None and audio_end is not None:
        total_duration = audio_end - audio_start
    else:
        total_duration = body.duration_seconds

    estimated_cost = len(prompts) * ESTIMATED_COST_PER_CLIP

    # Mark post as generating
    post_id_str = body.post_id
    if post_id_str:
        from amplify.db.models.post import PostModel as _StatusPost
        _sp_r = await db.execute(
            select(_StatusPost).where(
                _StatusPost.id == uuid.UUID(post_id_str),
                _StatusPost.tenant_id == tenant_id,
            )
        )
        _sp = _sp_r.scalar_one_or_none()
        if _sp:
            eng = dict(_sp.engagement or {})
            eng["ai_video_status"] = "generating"
            eng["ai_video_clips_total"] = len(prompts)
            eng["ai_video_clips_done"] = 0
            _sp.engagement = eng
            await db.flush()

    # Get DB session factory for background task
    session_factory = request.app.state.async_session

    # Fire off background generation and return immediately
    import asyncio
    asyncio.create_task(_run_ai_video_generation(
        session_factory=session_factory,
        tenant_id=tenant_id,
        user_id=user_id,
        post_id_str=post_id_str,
        prompts=prompts,
        audio_url=audio_url,
        image_url=image_url,
        total_duration=int(total_duration),
        aspect_ratio=body.aspect_ratio,
        replicate_api_token=settings.replicate_api_token,
        scene_durations=scene_durations,
        audio_start=audio_start,
        audio_end=audio_end,
        estimated_cost=estimated_cost,
        artist_name=artist_name,
        track_title=track_title,
        release_id=body.release_id,
        s3_bucket=settings.s3_bucket,
        s3_region=settings.s3_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        media_base_url=settings.media_base_url,
        duration_seconds=body.duration_seconds,
    ))

    return GenerateAIVideoResponse(
        status="generating",
        estimated_cost=estimated_cost,
        clips_generated=len(prompts),
    )


async def _run_ai_video_generation(
    *,
    session_factory,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    post_id_str: str | None,
    prompts: list[str],
    audio_url: str,
    image_url: str | None,
    total_duration: int,
    aspect_ratio: str,
    replicate_api_token: str,
    scene_durations: list[float] | None,
    audio_start: float | None,
    audio_end: float | None,
    estimated_cost: float,
    artist_name: str,
    track_title: str,
    release_id: str | None,
    s3_bucket: str,
    s3_region: str,
    aws_access_key_id: str,
    aws_secret_access_key: str,
    media_base_url: str,
    duration_seconds: int,
) -> None:
    """Background coroutine: generate AI video, upload to S3, update post."""
    import time
    import tempfile
    from sqlalchemy import select

    from app.services.replicate_video import generate_music_video

    start_time = time.time()

    async def _update_post_status(session, status: str, **extra):
        """Update the post's ai_video_status in engagement JSON."""
        if not post_id_str:
            return
        from amplify.db.models.post import PostModel
        r = await session.execute(
            select(PostModel).where(PostModel.id == uuid.UUID(post_id_str))
        )
        post = r.scalar_one_or_none()
        if post:
            eng = dict(post.engagement or {})
            eng["ai_video_status"] = status
            for k, v in extra.items():
                eng[k] = v
            post.engagement = eng
            if status == "complete" and extra.get("video_url"):
                post.media_urls = [extra["video_url"]]
            await session.flush()

    try:
        async with session_factory() as db:
            try:
                with tempfile.TemporaryDirectory(prefix="aivid_") as tmp_dir:
                    output_path = await generate_music_video(
                        prompts=prompts,
                        audio_url=audio_url,
                        image_url=image_url,
                        duration_seconds=total_duration,
                        aspect_ratio=aspect_ratio,
                        replicate_api_token=replicate_api_token,
                        output_dir=tmp_dir,
                        scene_durations=scene_durations,
                        audio_start=audio_start,
                        audio_end=audio_end,
                    )

                    # Upload to S3
                    from app.services.media_service import MediaService
                    media_svc = MediaService(
                        s3_bucket=s3_bucket,
                        s3_region=s3_region,
                        aws_access_key_id=aws_access_key_id,
                        aws_secret_access_key=aws_secret_access_key,
                        media_base_url=media_base_url,
                    )
                    with open(output_path, "rb") as f:
                        video_url = await media_svc.upload(
                            tenant_id, f, f"ai-video-{uuid.uuid4()}.mp4", "video/mp4"
                        )

                # Resolve campaign context for asset linking
                asset_artist_id = None
                asset_release_id = None
                asset_campaign_id = None
                if post_id_str:
                    from amplify.db.models.post import PostModel
                    from amplify.db.models.campaign import CampaignModel
                    post_q = await db.execute(
                        select(PostModel).where(PostModel.id == uuid.UUID(post_id_str))
                    )
                    linked_post = post_q.scalar_one_or_none()
                    if linked_post and linked_post.campaign_id:
                        asset_campaign_id = linked_post.campaign_id
                        camp_q = await db.execute(
                            select(CampaignModel).where(CampaignModel.id == linked_post.campaign_id)
                        )
                        linked_camp = camp_q.scalar_one_or_none()
                        if linked_camp:
                            asset_artist_id = linked_camp.artist_id
                            asset_release_id = linked_camp.release_id

                # Create asset with pending_review status
                from amplify.db.models.asset import AssetModel
                asset = AssetModel(
                    tenant_id=tenant_id,
                    artist_id=asset_artist_id,
                    release_id=asset_release_id,
                    campaign_id=asset_campaign_id,
                    asset_type="ai_video",
                    name=f"AI Video — {track_title or 'Untitled'}",
                    description=f"AI-generated video for {artist_name} — {track_title}",
                    file_url=video_url,
                    mime_type="video/mp4",
                    tags=["ai_video", "generated", "pending_review"],
                    source="ai_generated",
                    approval_status="pending_review",
                    generation_prompt="\n---\n".join(prompts),
                    generation_cost=estimated_cost,
                )
                db.add(asset)
                await db.flush()

                # Update post with completed video. Persist the audio_url
                # we actually used so an "Edit & regenerate" pass on this same
                # post will reuse it instead of falling through to the asset-
                # library matcher (which can pick the wrong song).
                await _update_post_status(
                    db,
                    "complete",
                    video_url=video_url,
                    ai_video_audio_url=audio_url,
                )

                # Record usage for billing
                try:
                    from amplify.billing.metering import MeteringService, METRIC_MEDIA_RENDERS
                    metering = MeteringService()
                    await metering.record_usage(str(tenant_id), METRIC_MEDIA_RENDERS, len(prompts))
                except Exception:
                    pass

                elapsed = int((time.time() - start_time) * 1000)
                logger.info(
                    "AI video generation complete: post=%s video=%s elapsed=%dms",
                    post_id_str, video_url, elapsed,
                )

                await db.commit()

            except Exception as exc:
                logger.exception("AI video background generation failed: %s", exc)
                await _update_post_status(db, "failed", ai_video_error=str(exc)[:500])
                await db.commit()

    except Exception as exc:
        logger.exception("AI video background task DB error: %s", exc)

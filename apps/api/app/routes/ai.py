"""AI content generation routes — wires ContentAgent and PlannerAgent to API."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.deps import get_db, get_settings, get_tenant_id, get_user_id, get_audit_service
from app.services.audit_service import AuditService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


# ── Request / Response schemas ───────────────────────────────────


class GenerateCaptionRequest(BaseModel):
    platform: str
    artist_name: str
    release_title: str = ""
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


async def _auto_generate_post_video(
    *,
    db: "AsyncSession",
    tenant_id: uuid.UUID,
    image_url: str,
    artist_id: uuid.UUID | None,
    release_id: uuid.UUID | None,
    content_hint: str,
    day_number: int,
    settings: Settings,
    duration: int = 15,
    force_audio_url: str | None = None,
    carousel_index: int | None = None,
) -> str | None:
    """Find the best audio asset and generate image+audio→video.

    Returns the uploaded video URL, or None if no audio available.
    For carousel items, carousel_index rotates through available tracks
    so each slide features a different song.
    """
    from amplify.db.models.asset import AssetModel
    from amplify.db.models.track import TrackModel
    from sqlalchemy import select, or_
    from app.services.media_service import MediaService
    from app.services.video_generator import create_post_video

    audio = None
    matched_track = None

    # If user forced a specific audio, use it directly
    if force_audio_url:
        # Find the asset to get its name
        aq = select(AssetModel).where(
            AssetModel.tenant_id == tenant_id,
            AssetModel.file_url == force_audio_url,
        ).limit(1)
        ar = await db.execute(aq)
        audio = ar.scalar_one_or_none()
        if not audio:
            # Create a minimal stand-in — URL is enough for video generation
            class _FakeAsset:
                file_url = force_audio_url
                name = "User-selected track"
            audio = _FakeAsset()

    if not audio:
        # Find audio assets for this artist/release
        q = select(AssetModel).where(
            AssetModel.tenant_id == tenant_id,
            AssetModel.asset_type == "audio",
            or_(AssetModel.approval_status != "rejected", AssetModel.approval_status.is_(None)),
        )
        if release_id:
            q = q.where(AssetModel.release_id == release_id)
        elif artist_id:
            q = q.where(AssetModel.artist_id == artist_id)
        q = q.order_by(AssetModel.created_at.desc()).limit(20)
        result = await db.execute(q)
        audio_assets = list(result.scalars().all())

        if not audio_assets:
            return None

        # Content-aware audio matching
        from app.services.content_pipeline import (
            _normalize_for_match, _any_phrase_match,
            _extract_track_reference, _build_track_name_map,
        )

        release_name_clean = ""
        if release_id:
            try:
                from amplify.db.models.release import ReleaseModel
                rq = select(ReleaseModel).where(
                    ReleaseModel.id == release_id,
                    ReleaseModel.tenant_id == tenant_id,
                )
                rr = await db.execute(rq)
                rel = rr.scalar_one_or_none()
                if rel and rel.name:
                    release_name_clean = _normalize_for_match(rel.name.lower())
            except Exception:
                pass

        # Build track name map for UUID-named audio assets
        track_url_to_title = await _build_track_name_map(db, tenant_id, release_id)

        # Extract explicit track reference from content
        explicit_track = _extract_track_reference(content_hint)
        explicit_track_clean = _normalize_for_match(explicit_track).lower() if explicit_track else ""

        # Normalize BEFORE lowering so camelCase splitting works on hashtags
        hint_lower = _normalize_for_match(content_hint).lower()
        hint_clean = hint_lower
        scored_audio = []
        for asset in audio_assets:
            score = 0
            raw_name = asset.name or ""
            import re as _re
            # Resolve UUID names via track table
            if _re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-", raw_name):
                track_title = track_url_to_title.get(asset.file_url, "")
                raw_name = track_title or asset.description or ""
            name_lower = raw_name.lower()
            name_clean = _normalize_for_match(name_lower)

            is_title_track = (
                release_name_clean
                and name_clean
                and (name_clean == release_name_clean
                     or name_clean in release_name_clean
                     or release_name_clean in name_clean)
            )

            # Priority 1: Explicit track reference match
            if explicit_track_clean and name_clean and len(name_clean) > 3:
                if name_clean in explicit_track_clean or explicit_track_clean in name_clean:
                    score += 80

            # Priority 2: Name appears in caption
            if name_clean and len(name_clean) > 3 and name_clean in hint_clean:
                score += 10 if is_title_track else 50
            elif _any_phrase_match(hint_lower, name_lower):
                score += 8 if is_title_track else 40

            for word in set(name_clean.split()):
                if len(word) > 3 and word in hint_clean:
                    score += 5

            scored_audio.append((score, asset))

        scored_audio.sort(key=lambda x: x[0], reverse=True)
        logger.info("Audio matching top 3: %s",
                     [(s, a.name) for s, a in scored_audio[:3]])

        if carousel_index is not None and len(scored_audio) > 1:
            # Carousel: rotate through top-scored tracks so each slide is different
            pick_idx = carousel_index % len(scored_audio)
            audio = scored_audio[pick_idx][1]
            logger.info("Carousel index %d → using track %d: %s",
                        carousel_index, pick_idx, audio.name)
        else:
            # Single post: use highest-scored audio
            audio = scored_audio[0][1] if scored_audio else audio_assets[0]

    # Smart clip selection: use lyrics to find verse/chorus boundaries
    audio_start = await _smart_audio_offset(
        db=db,
        tenant_id=tenant_id,
        release_id=release_id,
        audio_name=getattr(audio, "name", "") or "",
        day_number=day_number,
        duration=duration,
    )

    svc = MediaService(
        s3_bucket=settings.s3_bucket,
        s3_region=settings.s3_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        media_base_url=settings.media_base_url,
    )

    video_url = await create_post_video(
        image_url=image_url,
        audio_url=audio.file_url,
        tenant_id=tenant_id,
        media_service=svc,
        aspect_ratio="9:16",
        duration_seconds=duration,
        audio_start_seconds=audio_start,
    )

    asset_record = AssetModel(
        tenant_id=tenant_id,
        artist_id=artist_id,
        release_id=release_id,
        asset_type="video",
        name=f"Auto clip - {getattr(audio, 'name', 'track')} ({duration}s)",
        file_url=video_url,
        mime_type="video/mp4",
        tags=["auto_generated", "post_video"],
        source="ai_generated",
        approval_status="approved",
    )
    db.add(asset_record)

    return video_url


async def _smart_audio_offset(
    *,
    db: "AsyncSession",
    tenant_id: uuid.UUID,
    release_id: uuid.UUID | None,
    audio_name: str,
    day_number: int,
    duration: int,
) -> int:
    """Pick an audio start offset using lyrics to target verse/chorus sections.

    If lyrics are available, identifies section breaks (verse, chorus, bridge)
    and rotates through them. Falls back to varied offset if no lyrics.
    """
    from amplify.db.models.track import TrackModel
    from sqlalchemy import select
    from app.services.content_pipeline import _normalize_for_match

    # Try to find the matching track with lyrics
    if release_id and audio_name:
        try:
            tq = select(TrackModel).where(
                TrackModel.release_id == release_id,
                TrackModel.tenant_id == tenant_id,
            )
            tr = await db.execute(tq)
            tracks = list(tr.scalars().all())

            name_clean = _normalize_for_match(audio_name.lower())
            matched = None
            for t in tracks:
                if _normalize_for_match(t.title.lower()) in name_clean or name_clean in _normalize_for_match(t.title.lower()):
                    matched = t
                    break

            if matched and matched.lyrics and matched.duration_seconds:
                sections = _find_lyric_sections(matched.lyrics, matched.duration_seconds)
                if sections:
                    # Rotate through sections using day_number
                    section_start = sections[day_number % len(sections)]
                    # Ensure we don't go past the end
                    max_start = max(0, matched.duration_seconds - duration)
                    return min(section_start, max_start)
        except Exception:
            pass

    # Fallback: varied offset that avoids intros
    # Cycle through: verse1 (~20s), chorus (~45s), verse2 (~75s), bridge (~120s)
    offsets = [20, 45, 75, 120, 30, 60, 90, 105]
    return offsets[day_number % len(offsets)]


def _find_lyric_sections(lyrics: str, duration_seconds: int) -> list[int]:
    """Detect verse/chorus/bridge boundaries from lyrics text.

    Returns list of approximate timestamps (in seconds) for section starts.
    Uses blank lines and common section markers as boundaries.
    """
    lines = lyrics.strip().split("\n")
    if not lines:
        return []

    # Find section breaks: blank lines, [Verse], [Chorus], etc.
    section_starts: list[int] = []
    current_line = 0
    total_content_lines = sum(1 for l in lines if l.strip())
    if total_content_lines == 0:
        return []

    # Estimate seconds per content line
    secs_per_line = duration_seconds / total_content_lines
    elapsed = 0.0
    in_section_gap = True  # Start at beginning of first section

    for line in lines:
        stripped = line.strip()

        # Detect section markers
        is_marker = (
            not stripped  # blank line = section break
            or stripped.startswith("[")  # [Verse 1], [Chorus], etc.
            or stripped.lower() in ("verse", "chorus", "bridge", "outro", "intro", "pre-chorus", "hook")
        )

        if is_marker:
            in_section_gap = True
            if not stripped:
                continue
            # Section label like [Chorus] — next content line starts section
            continue

        if in_section_gap:
            # First content line after a gap = section start
            start_sec = int(elapsed)
            if start_sec > 5:  # Skip very beginning (likely intro)
                section_starts.append(start_sec)
            in_section_gap = False

        elapsed += secs_per_line

    # Always include a few standard offsets as fallbacks
    if not section_starts:
        # No clear sections — estimate common song structure
        section_starts = [
            int(duration_seconds * 0.08),   # verse 1
            int(duration_seconds * 0.25),   # chorus 1
            int(duration_seconds * 0.42),   # verse 2
            int(duration_seconds * 0.58),   # chorus 2
            int(duration_seconds * 0.75),   # bridge
        ]

    return section_starts


def _extract_lyrics_for_segment(
    full_lyrics: str,
    audio_start: int,
    duration: int,
    track_duration: int | None = None,
) -> str:
    """Extract the lyrics that correspond to a specific audio segment.

    Given full song lyrics, audio_start offset, and clip duration,
    returns only the lines that would be sung during that segment.
    This prevents dumping all 40+ lines into a 15-second clip.

    Timing is estimated by dividing song duration by total lyric lines.
    """
    all_lines = full_lyrics.strip().split("\n")

    # Filter to content lines only (skip blanks and markers) but track original indices
    content_lines: list[tuple[int, str]] = []
    for i, line in enumerate(all_lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            continue
        if stripped.lower() in ("verse", "chorus", "bridge", "outro", "intro", "pre-chorus", "hook"):
            continue
        content_lines.append((i, stripped))

    if not content_lines:
        return full_lyrics

    # If we don't know the track duration, estimate ~3.5s per lyric line
    if not track_duration or track_duration <= 0:
        track_duration = int(len(content_lines) * 3.5)

    secs_per_line = track_duration / len(content_lines)
    audio_end = audio_start + duration

    # Find lines that fall within the audio segment
    segment_lines: list[str] = []
    for idx, (_, line) in enumerate(content_lines):
        line_start = idx * secs_per_line
        line_end = line_start + secs_per_line
        # Include line if it overlaps with our segment
        if line_end > audio_start and line_start < audio_end:
            segment_lines.append(line)

    # If we got too few lines (e.g. audio_start beyond lyrics), fall back
    # to a reasonable chunk from the middle
    if len(segment_lines) < 2 and len(content_lines) > 4:
        # Pick lines from the middle third
        mid = len(content_lines) // 3
        count = max(4, duration // 3)  # ~1 line per 3 seconds
        segment_lines = [line for _, line in content_lines[mid:mid + count]]

    # Cap at a reasonable number: ~1 line per 2-3 seconds
    max_lines = max(3, duration // 2)
    if len(segment_lines) > max_lines:
        segment_lines = segment_lines[:max_lines]

    return "\n".join(segment_lines)


async def _generate_lyric_video_for_post(
    *,
    db: "AsyncSession",
    tenant_id: uuid.UUID,
    post,
    image_url: str,
    force_audio_url: str | None,
    artist_id: uuid.UUID | None,
    release_id: uuid.UUID | None,
    settings: "Settings",
    duration: int = 30,
) -> str | None:
    """Generate a lyric video for a post — image + audio + lyrics overlay.

    Returns uploaded video URL, or None if lyrics/audio unavailable.
    """
    from amplify.db.models.track import TrackModel
    from amplify.db.models.asset import AssetModel
    from amplify.db.models.release import ReleaseModel
    from sqlalchemy import select, or_
    from app.services.media_service import MediaService
    from app.services.video_generator import create_post_video
    from app.services.content_pipeline import _normalize_for_match

    # Resolve the track — match by caption content
    from app.services.content_pipeline import _extract_track_reference
    content_text = post.content_text or ""
    # Normalize BEFORE lowering so camelCase hashtags get split
    content_clean = _normalize_for_match(content_text).lower()

    # Extract explicit track reference (e.g. "🎵 Featuring: Good Boy")
    explicit_track = _extract_track_reference(content_text)
    explicit_track_clean = _normalize_for_match(explicit_track).lower() if explicit_track else ""

    matched_track = None
    audio_url = force_audio_url
    lyrics = ""
    artist_name = ""
    track_title = ""

    if release_id:
        tq = select(TrackModel).where(
            TrackModel.release_id == release_id,
            TrackModel.tenant_id == tenant_id,
        ).order_by(TrackModel.track_number)
        tr = await db.execute(tq)
        tracks = list(tr.scalars().all())

        # Priority 1: Match explicit track reference ("Featuring: X")
        if explicit_track_clean:
            for t in tracks:
                title_clean = _normalize_for_match(t.title.lower())
                if title_clean and (title_clean in explicit_track_clean or explicit_track_clean in title_clean):
                    matched_track = t
                    logger.info("Lyric video: explicit track match '%s' → '%s'", explicit_track, t.title)
                    break

        # Priority 2: Find track title mentioned in caption
        if not matched_track:
            for t in tracks:
                title_clean = _normalize_for_match(t.title.lower())
                if title_clean and len(title_clean) > 3 and title_clean in content_clean:
                    matched_track = t
                    break

        # Fallback: round-robin
        if not matched_track and tracks:
            matched_track = tracks[(post.day_number or 1) % len(tracks)]

        if matched_track:
            track_title = matched_track.title
            lyrics = matched_track.lyrics or ""
            if not audio_url and matched_track.audio_url:
                audio_url = matched_track.audio_url

    # If still no audio URL, find from assets
    if not audio_url and release_id:
        aq = select(AssetModel).where(
            AssetModel.tenant_id == tenant_id,
            AssetModel.asset_type == "audio",
            AssetModel.release_id == release_id,
            or_(AssetModel.approval_status != "rejected", AssetModel.approval_status.is_(None)),
        ).limit(1)
        ar = await db.execute(aq)
        audio_asset = ar.scalar_one_or_none()
        if audio_asset:
            audio_url = audio_asset.file_url

    if not audio_url or not lyrics:
        logger.info("Lyric video skipped for post %s: audio=%s lyrics=%s",
                     post.id, bool(audio_url), bool(lyrics))
        return None

    # Get artist name
    if artist_id:
        from amplify.db.models.artist import ArtistModel
        aq = select(ArtistModel).where(ArtistModel.id == artist_id)
        ar = await db.execute(aq)
        artist = ar.scalar_one_or_none()
        if artist:
            artist_name = artist.name

    # Pick audio offset — use smart section detection from lyrics
    audio_start = await _smart_audio_offset(
        db=db,
        tenant_id=tenant_id,
        release_id=release_id,
        audio_name=track_title or "",
        day_number=post.day_number or 1,
        duration=duration,
    )

    # Extract only the lyrics for the audio segment being played
    segment_lyrics = _extract_lyrics_for_segment(
        lyrics, audio_start, duration,
        track_duration=matched_track.duration_seconds if matched_track else None,
    )

    logger.info("Lyric video for post %s: track='%s', offset=%ds, %d lines",
                post.id, track_title, audio_start, segment_lyrics.count("\n") + 1)

    # Use the lyric video generator
    import tempfile
    from pathlib import Path
    from app.services.video_generator import generate_lyric_video, download_url_to_file

    svc = MediaService(
        s3_bucket=settings.s3_bucket,
        s3_region=settings.s3_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        media_base_url=settings.media_base_url,
    )

    with tempfile.TemporaryDirectory(prefix="amplify-lyric-") as tmpdir:
        img_path = str(Path(tmpdir) / "image.jpg")
        audio_path = str(Path(tmpdir) / "audio.wav")
        output_path = str(Path(tmpdir) / "lyric-video.mp4")

        await download_url_to_file(image_url, img_path)
        await download_url_to_file(audio_url, audio_path)

        await generate_lyric_video(
            image_path=img_path,
            audio_path=audio_path,
            lyrics=segment_lyrics,
            output_path=output_path,
            aspect_ratio="9:16",
            duration_seconds=duration,
            audio_start_seconds=audio_start,
            artist_name=artist_name,
            track_title=track_title,
        )

        with open(output_path, "rb") as f:
            url = await svc.upload(
                tenant_id,
                f,
                f"lyric-video-{uuid.uuid4().hex[:8]}.mp4",
                "video/mp4",
            )

    # Save as asset
    asset = AssetModel(
        tenant_id=tenant_id,
        artist_id=artist_id,
        release_id=release_id,
        asset_type="lyric_video",
        name=f"Lyric video - {track_title} ({duration}s)",
        file_url=url,
        mime_type="video/mp4",
        tags=["auto_generated", "lyric_video"],
        source="ai_generated",
        approval_status="approved",
    )
    db.add(asset)

    logger.info("Lyric video generated for post %s: %s", post.id, url)
    return url


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
            key_message=body.key_message,
            cta_destination="",
            tone=body.tone,
            brand_voice=body.brand_voice,
            hashtags=body.hashtags or None,
        )

        variants = []
        if result.structured and hasattr(result.structured, "variants"):
            for v in result.structured.variants:
                variants.append(CaptionVariant(
                    variant_id=v.variant_id,
                    headline=getattr(v, "headline", "") or "",
                    body=getattr(v, "body", "") or "",
                    hashtags=getattr(v, "hashtags", []) or [],
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



# ── Static Video (Tier 1) Generation ──────────────────────────


class GenerateStaticVideoRequest(BaseModel):
    post_id: str
    duration_seconds: int = 15
    aspect_ratio: str = "9:16"


class GenerateStaticVideoResponse(BaseModel):
    video_url: str = ""
    asset_id: str | None = None
    elapsed_ms: int = 0


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
                for mu in _linked.media_urls:
                    if not image_url and any(mu.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
                        image_url = mu
                    if not audio_url and any(mu.lower().endswith(ext) for ext in (".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a")):
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

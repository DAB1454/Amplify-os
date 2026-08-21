"""Post media generation helpers — lyric/static video, lifted from the API
(apps/api/app/routes/ai.py) into the shared media package so the worker can
generate post media too. Behavior-preserving move.

`settings` is duck-typed: any object exposing s3_bucket / s3_region /
aws_access_key_id / aws_secret_access_key / media_base_url (and, for AI
video, replicate_api_token) works — so both the API and worker Settings
objects are accepted.
"""
from __future__ import annotations

import logging
import tempfile
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


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
    track_id: uuid.UUID | None = None,
) -> str | None:
    """Find the best audio asset and generate image+audio→video.

    Returns the uploaded video URL, or None if no audio available.
    For carousel items, carousel_index rotates through available tracks
    so each slide features a different song.

    If ``track_id`` is provided, the linked track's audio is used directly
    and fuzzy caption scoring is bypassed entirely. This is the
    track-anchored path the planner uses.
    """
    from amplify.db.models.asset import AssetModel
    from amplify.db.models.track import TrackModel
    from sqlalchemy import select, or_
    from amplify.media.storage import MediaService
    from amplify.media.video_generator import create_post_video

    audio = None
    matched_track = None

    # Hard anchor: resolve audio from the linked track row. Skipped when
    # the caller already forced a specific audio URL (manual override wins).
    if track_id and not force_audio_url:
        tq = select(TrackModel).where(
            TrackModel.id == track_id,
            TrackModel.tenant_id == tenant_id,
        ).limit(1)
        tr = await db.execute(tq)
        anchored_track = tr.scalar_one_or_none()
        if anchored_track and anchored_track.audio_url:
            force_audio_url = anchored_track.audio_url
            matched_track = anchored_track
            logger.info(
                "Post video: anchored to track_id %s (%s) — audio_url=%s",
                track_id, anchored_track.title, anchored_track.audio_url,
            )

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
        from amplify.agents.pipeline.content import (
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

        # ── Usage-aware penalty: penalize tracks already used in this
        # campaign so we don't always pick the same song ──────────────
        for i, (score, asset) in enumerate(scored_audio):
            asset_uses = getattr(asset, "uses_count", 0) or 0
            scored_audio[i] = (score - min(asset_uses * 8, 40), asset)

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
            # Rotate among top candidates using day_number for variety
            # instead of always picking the single highest scorer
            top = scored_audio[0][0]
            close = [(s, a) for s, a in scored_audio if s >= top - 20 and top > 0]
            if len(close) > 1 and day_number:
                audio = close[day_number % len(close)][1]
            else:
                audio = scored_audio[0][1] if scored_audio else audio_assets[0]

    # Track audio usage for future dedup
    if hasattr(audio, "uses_count"):
        audio.uses_count = (audio.uses_count or 0) + 1
        audio.last_used_at = datetime.utcnow()

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
    and rotates through them. Uses a hash of (track_name, day_number) to select
    the section, so different tracks on the same day get different offsets,
    and the same track on different days gets different offsets.

    Falls back to proportional offset within the track duration if no lyrics.
    """
    from amplify.db.models.track import TrackModel
    from sqlalchemy import select
    from amplify.agents.pipeline.content import _normalize_for_match
    import hashlib

    track_duration = None

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

            if matched:
                track_duration = matched.duration_seconds

            if matched and matched.lyrics and matched.duration_seconds:
                sections = _find_lyric_sections(matched.lyrics, matched.duration_seconds)
                if sections:
                    # Hash track name + day_number for varied but deterministic selection.
                    # This ensures: same track on different days → different section,
                    # different tracks on the same day → different section.
                    seed = hashlib.md5(f"{audio_name}:{day_number}".encode()).digest()
                    idx = int.from_bytes(seed[:4], "little") % len(sections)
                    section_start = sections[idx]
                    max_start = max(0, matched.duration_seconds - duration)
                    return min(section_start, max_start)
        except Exception:
            pass

    # Fallback: use track duration if known, otherwise assume 210s (3:30)
    total = track_duration or 210
    max_start = max(0, total - duration)

    # Generate a pseudo-random offset using hash of track + day for variety.
    # Avoids the first 10s (intros) and last `duration` seconds.
    import hashlib
    seed = hashlib.md5(f"{audio_name}:{day_number}:offset".encode()).digest()
    raw = int.from_bytes(seed[:4], "little")
    safe_start = 10  # skip intros
    if max_start > safe_start:
        return safe_start + (raw % (max_start - safe_start))
    return safe_start


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
    from amplify.media.storage import MediaService
    from amplify.media.video_generator import create_post_video
    from amplify.agents.pipeline.content import _normalize_for_match

    # Resolve the track — match by caption content
    from amplify.agents.pipeline.content import _extract_track_reference
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

    # Priority -1: if the post has a hard track_id anchor (set by the
    # planner or the manual "Track-first" create form), trust it absolutely.
    # No fuzzy caption matching needed — we know exactly which track this
    # post is about.
    post_track_id = getattr(post, "track_id", None)
    if post_track_id:
        tq = select(TrackModel).where(
            TrackModel.id == post_track_id,
            TrackModel.tenant_id == tenant_id,
        ).limit(1)
        tr = await db.execute(tq)
        anchored = tr.scalar_one_or_none()
        if anchored:
            matched_track = anchored
            track_title = anchored.title
            lyrics = anchored.lyrics or ""
            if not audio_url and anchored.audio_url:
                audio_url = anchored.audio_url
            logger.info(
                "Lyric video: anchored to track_id %s (%s, lyrics=%s chars)",
                post_track_id, anchored.title, len(lyrics),
            )

    # Priority 0: if the user picked an explicit audio URL, find THAT track
    # so lyrics line up with the audio. Caption-based matching can pick a
    # different track than the one whose audio we're about to play, which
    # is why the manual lyric_video flow was producing image-only posts
    # (lyrics empty → generator returned None → media_urls stayed [image]).
    if audio_url and not matched_track:
        url_match_q = select(TrackModel).where(
            TrackModel.tenant_id == tenant_id,
            TrackModel.audio_url == audio_url,
        ).limit(1)
        url_match_r = await db.execute(url_match_q)
        url_matched = url_match_r.scalar_one_or_none()
        if url_matched:
            matched_track = url_matched
            track_title = url_matched.title
            lyrics = url_matched.lyrics or ""
            logger.info(
                "Lyric video: matched track '%s' by audio_url (lyrics=%s chars)",
                url_matched.title, len(lyrics),
            )

    if release_id and not matched_track:
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

    # Prefer track-specific album art over whatever the caller passed in.
    # The caller's image_url comes from a caption-based asset match, which
    # often picks the wrong cover when a library has multiple artworks —
    # the lyrics + audio end up right but the visual is wrong. If we can
    # find an album_art asset whose name/tags mention the matched track
    # title, use that instead. Fall back to the release artwork, then
    # finally to the caller's pick.
    if matched_track and track_title:
        try:
            title_clean = _normalize_for_match(track_title.lower())
            art_q = select(AssetModel).where(
                AssetModel.tenant_id == tenant_id,
                AssetModel.release_id == release_id,
                AssetModel.asset_type.in_(["album_art", "image", "promo_photo"]),
                or_(
                    AssetModel.approval_status != "rejected",
                    AssetModel.approval_status.is_(None),
                ),
            )
            art_rows = list((await db.execute(art_q)).scalars().all())
            track_specific = None
            for a in art_rows:
                name_clean = _normalize_for_match((a.name or "").lower())
                if title_clean and title_clean in name_clean:
                    track_specific = a
                    break
                # also check tags
                tags = a.tags or []
                if any(title_clean in _normalize_for_match(str(t).lower()) for t in tags):
                    track_specific = a
                    break
            if track_specific:
                logger.info(
                    "Lyric video: overriding image with track-specific art for '%s' (%s)",
                    track_title, track_specific.id,
                )
                image_url = track_specific.file_url
            elif release_id:
                rel_q = select(ReleaseModel).where(ReleaseModel.id == release_id)
                rel = (await db.execute(rel_q)).scalar_one_or_none()
                if rel and rel.artwork_url:
                    logger.info(
                        "Lyric video: falling back to release artwork for '%s'",
                        track_title,
                    )
                    image_url = rel.artwork_url
        except Exception as exc:
            logger.warning("Lyric video art lookup failed: %s", exc)

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
    from amplify.media.video_generator import generate_lyric_video, download_url_to_file

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



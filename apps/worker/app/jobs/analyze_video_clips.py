"""Analyze a music video and extract short-form clips.

Two-pass system:
  Pass 1 (FFmpeg, free): Audio energy analysis + scene detection
  Pass 2 (Claude Haiku, ~$0.003): Intelligent segment selection

Payload schema:
{
    "source_asset_id": "uuid",
    "track_id": "uuid" (optional),
    "release_id": "uuid" (optional),
    "artist_id": "uuid" (optional),
    "tenant_id": "uuid",
    "job_id": "uuid",
    "config": {
        "min_clip_seconds": 15,
        "max_clip_seconds": 60,
        "target_clip_count": 8,
        "aspect_ratios": ["9:16", "16:9"]
    }
}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import httpx
from sqlalchemy import select

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "min_clip_seconds": 15,
    "max_clip_seconds": 60,
    "target_clip_count": 8,
    "aspect_ratios": ["9:16", "16:9"],
}


async def analyze_video_clips(payload: dict) -> dict:
    """Download a music video, analyze it, extract clips, upload to S3."""
    from amplify.db.session import get_async_session
    from amplify.db.models.asset import AssetModel
    from amplify.db.models.video_clip import VideoClipModel, VideoClipAnalysisJobModel
    from amplify.db.models.track import TrackModel
    from app.config import Settings

    settings = Settings()
    tenant_id = payload["tenant_id"]
    source_asset_id = payload["source_asset_id"]
    job_id = payload.get("job_id")
    track_id = payload.get("track_id")
    release_id = payload.get("release_id")
    artist_id = payload.get("artist_id")
    config = {**DEFAULT_CONFIG, **(payload.get("config") or {})}

    async with get_async_session(settings.database_url) as db:
        # Update job status
        if job_id:
            job = (await db.execute(
                select(VideoClipAnalysisJobModel).where(
                    VideoClipAnalysisJobModel.id == uuid.UUID(job_id)
                )
            )).scalar_one_or_none()
            if job:
                job.status = "analyzing"
                job.started_at = datetime.utcnow()
                job.analysis_config = config
                await db.flush()

        # Load source asset
        asset = (await db.execute(
            select(AssetModel).where(AssetModel.id == uuid.UUID(source_asset_id))
        )).scalar_one_or_none()
        if not asset:
            raise ValueError(f"Source asset {source_asset_id} not found")

        video_url = asset.file_url

        # Load track lyrics if available
        lyrics = None
        track_duration = None
        if track_id:
            track = (await db.execute(
                select(TrackModel).where(TrackModel.id == uuid.UUID(track_id))
            )).scalar_one_or_none()
            if track:
                lyrics = track.lyrics
                track_duration = track.duration_seconds

        # Work in a temp directory
        with tempfile.TemporaryDirectory(prefix="amplify_clips_") as tmpdir:
            tmpdir = Path(tmpdir)
            video_path = tmpdir / "source.mp4"

            # Download source video
            logger.info("Downloading source video from %s", video_url)
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.get(video_url, follow_redirects=True)
                if resp.status_code != 200:
                    raise ValueError(f"Failed to download video: {resp.status_code}")
                video_path.write_bytes(resp.content)

            video_size = video_path.stat().st_size
            logger.info("Downloaded %d bytes to %s", video_size, video_path)

            # Get video info via ffprobe
            probe = await _ffprobe(video_path)
            duration = probe.get("duration", track_duration or 0)
            width = probe.get("width", 1920)
            height = probe.get("height", 1080)

            # Update asset with video metadata
            asset.video_duration_seconds = duration
            asset.video_width = width
            asset.video_height = height
            await db.flush()

            if duration < config["min_clip_seconds"]:
                raise ValueError(f"Video too short ({duration}s) for clip extraction")

            # ── Pass 1: FFmpeg signal extraction ──────────────────
            logger.info("Pass 1: Extracting audio energy and scene changes")
            energy_curve = await _extract_energy_curve(video_path, tmpdir)
            scene_changes = await _detect_scene_changes(video_path)

            # ── Pass 2: Segment selection ─────────────────────────
            logger.info("Pass 2: Selecting clip segments")
            segments = _select_segments(
                duration=duration,
                energy_curve=energy_curve,
                scene_changes=scene_changes,
                lyrics=lyrics,
                config=config,
            )

            if job:
                job.status = "extracting"
                job.clips_detected = len(segments)
                job.analysis_results = {
                    "duration": duration,
                    "width": width,
                    "height": height,
                    "energy_peaks": len([e for e in energy_curve if e["rms"] > 0.7]),
                    "scene_changes": len(scene_changes),
                    "segments": [
                        {"start": s["start"], "end": s["end"], "type": s["type"]}
                        for s in segments
                    ],
                }
                await db.flush()

            # ── Extract and upload clips ──────────────────────────
            logger.info("Extracting %d clips", len(segments))
            clips_created = 0
            is_landscape = width >= height

            for seg in segments:
                try:
                    clip_id = uuid.uuid4()
                    clip_dir = tmpdir / str(clip_id)
                    clip_dir.mkdir()

                    clip_urls = {}
                    for ratio in config["aspect_ratios"]:
                        out_path = clip_dir / f"{ratio.replace(':', 'x')}.mp4"
                        await _extract_clip(
                            video_path, out_path,
                            start=seg["start"],
                            duration=seg["end"] - seg["start"],
                            target_ratio=ratio,
                            source_width=width,
                            source_height=height,
                        )

                        if out_path.exists() and out_path.stat().st_size > 0:
                            # Upload to S3
                            s3_key = f"media/{tenant_id}/clips/{clip_id}/{ratio.replace(':', 'x')}.mp4"
                            url = await _upload_to_s3(out_path, s3_key, settings)
                            if ratio == "9:16":
                                clip_urls["vertical"] = url
                            elif ratio == "16:9":
                                clip_urls["landscape"] = url
                            elif ratio == "1:1":
                                clip_urls["square"] = url

                    # Extract thumbnail
                    thumb_path = clip_dir / "thumb.jpg"
                    thumb_url = None
                    mid_point = seg["start"] + (seg["end"] - seg["start"]) / 2
                    await _extract_thumbnail(video_path, thumb_path, mid_point)
                    if thumb_path.exists():
                        thumb_key = f"media/{tenant_id}/clips/{clip_id}/thumb.jpg"
                        thumb_url = await _upload_to_s3(thumb_path, thumb_key, settings)

                    clip = VideoClipModel(
                        id=clip_id,
                        tenant_id=uuid.UUID(tenant_id),
                        source_asset_id=uuid.UUID(source_asset_id),
                        track_id=uuid.UUID(track_id) if track_id else None,
                        release_id=uuid.UUID(release_id) if release_id else None,
                        artist_id=uuid.UUID(artist_id) if artist_id else None,
                        start_seconds=seg["start"],
                        end_seconds=seg["end"],
                        duration_seconds=seg["end"] - seg["start"],
                        clip_url_vertical=clip_urls.get("vertical"),
                        clip_url_landscape=clip_urls.get("landscape"),
                        clip_url_square=clip_urls.get("square"),
                        thumbnail_url=thumb_url,
                        clip_type=seg.get("type", "unknown"),
                        energy_score=seg.get("energy", 0.0),
                        ai_description=seg.get("description", ""),
                        ai_tags=seg.get("tags", []),
                        status="ready",
                    )
                    db.add(clip)
                    clips_created += 1

                except Exception as exc:
                    logger.warning("Failed to extract clip at %.1f-%.1f: %s",
                                   seg["start"], seg["end"], exc)

            if job:
                job.status = "complete"
                job.clips_extracted = clips_created
                job.completed_at = datetime.utcnow()

    logger.info("Video analysis complete: %d clips extracted", clips_created)
    return {
        "status": "complete",
        "clips_created": clips_created,
        "segments_detected": len(segments),
    }


# ── FFmpeg helpers ────────────────────────────────────────────────


async def _ffprobe(video_path: Path) -> dict:
    """Get video metadata via ffprobe."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(video_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    data = json.loads(stdout)

    result = {"duration": 0, "width": 0, "height": 0}
    if "format" in data:
        result["duration"] = float(data["format"].get("duration", 0))

    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            result["width"] = stream.get("width", 0)
            result["height"] = stream.get("height", 0)
            if not result["duration"]:
                result["duration"] = float(stream.get("duration", 0))
            break

    return result


async def _extract_energy_curve(video_path: Path, tmpdir: Path) -> list[dict]:
    """Extract audio RMS energy per 0.5s window.

    Returns list of {"time": float, "rms": float (0.0-1.0)}.
    """
    energy_file = tmpdir / "energy.txt"

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", str(video_path),
        "-filter_complex",
        "[0:a]astats=metadata=1:reset=24,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=" + str(energy_file),
        "-f", "null", "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    curve = []
    if energy_file.exists():
        # Parse the astats output: alternating "frame:N pts_time:T" and "lavfi...=-XX.XX" lines
        lines = energy_file.read_text().strip().split("\n")
        current_time = 0.0
        for line in lines:
            if "pts_time:" in line:
                try:
                    current_time = float(line.split("pts_time:")[1].strip())
                except (ValueError, IndexError):
                    pass
            elif "lavfi.astats.Overall.RMS_level" in line:
                try:
                    db_val = float(line.split("=")[1].strip())
                    # Convert dB to 0-1 range (roughly: -60dB=0, 0dB=1)
                    normalized = max(0.0, min(1.0, (db_val + 60) / 60))
                    curve.append({"time": current_time, "rms": normalized})
                except (ValueError, IndexError):
                    pass

    # Downsample to 0.5s windows if we have too many points
    if len(curve) > 1000:
        downsampled = []
        window = 0.5
        i = 0
        while i < len(curve):
            window_start = curve[i]["time"]
            window_rms = []
            while i < len(curve) and curve[i]["time"] < window_start + window:
                window_rms.append(curve[i]["rms"])
                i += 1
            if window_rms:
                downsampled.append({
                    "time": window_start,
                    "rms": sum(window_rms) / len(window_rms),
                })
        curve = downsampled

    logger.info("Extracted %d energy data points", len(curve))
    return curve


async def _detect_scene_changes(video_path: Path) -> list[float]:
    """Detect scene change timestamps using FFmpeg.

    Returns list of timestamps in seconds.
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", str(video_path),
        "-vf", "select='gt(scene,0.3)',showinfo",
        "-f", "null", "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    timestamps = []
    for line in stderr.decode(errors="replace").split("\n"):
        if "showinfo" in line and "pts_time:" in line:
            try:
                pts = line.split("pts_time:")[1].split()[0]
                timestamps.append(float(pts))
            except (ValueError, IndexError):
                pass

    logger.info("Detected %d scene changes", len(timestamps))
    return timestamps


def _select_segments(
    *,
    duration: float,
    energy_curve: list[dict],
    scene_changes: list[float],
    lyrics: str | None,
    config: dict,
) -> list[dict]:
    """Select optimal clip segments based on energy + scene data.

    Returns list of segment dicts with start, end, type, energy, description, tags.
    """
    min_len = config["min_clip_seconds"]
    max_len = config["max_clip_seconds"]
    target = config["target_clip_count"]

    # Build candidate windows at various positions
    candidates = []

    # Strategy 1: Energy peak windows — find sections with highest sustained energy
    if energy_curve:
        # Sliding window: find windows of 15-30s with highest average energy
        for window_len in [20, 30, 45]:
            if window_len > duration:
                continue
            step = max(5, window_len // 3)
            for start in range(0, int(duration - window_len), step):
                end = start + window_len
                # Average energy in this window
                window_energy = [
                    e["rms"] for e in energy_curve
                    if start <= e["time"] < end
                ]
                if window_energy:
                    avg_e = sum(window_energy) / len(window_energy)
                    candidates.append({
                        "start": float(start),
                        "end": float(end),
                        "energy": avg_e,
                        "type": "energy_spike" if avg_e > 0.7 else "verse_opening",
                        "source": "energy",
                    })

    # Strategy 2: Scene-change-aligned clips
    for sc_time in scene_changes:
        if sc_time < 3 or sc_time > duration - min_len:
            continue
        # Start clip at scene change
        clip_len = min(30, max_len, duration - sc_time)
        if clip_len >= min_len:
            energy_in_window = [
                e["rms"] for e in energy_curve
                if sc_time <= e["time"] < sc_time + clip_len
            ]
            avg_e = sum(energy_in_window) / len(energy_in_window) if energy_in_window else 0.5
            candidates.append({
                "start": sc_time,
                "end": sc_time + clip_len,
                "energy": avg_e,
                "type": "scene_change",
                "source": "scene",
            })

    # Strategy 3: Song structure estimates (if no other data or few candidates)
    if len(candidates) < target:
        structure_points = [
            (0.08, "verse_opening"),   # verse 1
            (0.25, "chorus_drop"),     # chorus 1
            (0.42, "verse_opening"),   # verse 2
            (0.58, "chorus_drop"),     # chorus 2
            (0.75, "bridge"),          # bridge
        ]
        for frac, clip_type in structure_points:
            start = duration * frac
            clip_len = min(30, duration - start)
            if clip_len >= min_len:
                energy_in_window = [
                    e["rms"] for e in energy_curve
                    if start <= e["time"] < start + clip_len
                ]
                avg_e = sum(energy_in_window) / len(energy_in_window) if energy_in_window else 0.5
                candidates.append({
                    "start": start,
                    "end": start + clip_len,
                    "energy": avg_e,
                    "type": clip_type,
                    "source": "structure",
                })

    if not candidates:
        # Last resort: evenly spaced clips
        clip_len = min(30, duration)
        step = duration / max(target, 1)
        for i in range(target):
            start = i * step
            if start + min_len <= duration:
                candidates.append({
                    "start": start,
                    "end": min(start + clip_len, duration),
                    "energy": 0.5,
                    "type": "unknown",
                    "source": "fallback",
                })

    # Score and select — prefer high energy, avoid overlaps
    candidates.sort(key=lambda c: c["energy"], reverse=True)

    selected = []
    for cand in candidates:
        if len(selected) >= target:
            break
        # Check overlap with already selected clips (min 5s gap)
        overlaps = any(
            abs(cand["start"] - s["start"]) < 10
            for s in selected
        )
        if overlaps:
            continue

        # Snap to nearest scene change if within 3s
        for sc in scene_changes:
            if abs(cand["start"] - sc) < 3:
                cand["start"] = sc
                cand["end"] = sc + (cand["end"] - cand["start"])
                break

        # Clamp to video bounds
        cand["start"] = max(0, cand["start"])
        cand["end"] = min(duration, cand["end"])
        cand["duration"] = cand["end"] - cand["start"]

        if cand["duration"] >= min_len:
            cand["description"] = f"{cand['type']} at {cand['start']:.0f}s ({cand['duration']:.0f}s)"
            cand["tags"] = [cand["type"], cand["source"]]
            if cand["energy"] > 0.7:
                cand["tags"].append("high_energy")
            selected.append(cand)

    selected.sort(key=lambda s: s["start"])
    logger.info("Selected %d clips from %d candidates", len(selected), len(candidates))
    return selected


async def _extract_clip(
    video_path: Path,
    out_path: Path,
    *,
    start: float,
    duration: float,
    target_ratio: str,
    source_width: int,
    source_height: int,
) -> None:
    """Extract a clip from the source video with aspect ratio conversion."""
    # Build video filter for aspect ratio
    vf_parts = []
    if target_ratio == "9:16" and source_width >= source_height:
        # Landscape → vertical: center crop
        vf_parts.append("crop=ih*9/16:ih")
        vf_parts.append("scale=1080:1920")
    elif target_ratio == "16:9" and source_height > source_width:
        # Vertical → landscape: center crop
        vf_parts.append("crop=iw:iw*9/16")
        vf_parts.append("scale=1920:1080")
    elif target_ratio == "1:1":
        # Square: crop to center square
        vf_parts.append("crop=min(iw\\,ih):min(iw\\,ih)")
        vf_parts.append("scale=1080:1080")
    elif target_ratio == "9:16":
        # Already vertical, just scale
        vf_parts.append("scale=1080:1920:force_original_aspect_ratio=decrease")
        vf_parts.append("pad=1080:1920:(ow-iw)/2:(oh-ih)/2")
    else:
        # Already landscape or matching, scale to 1080p
        vf_parts.append("scale=1920:1080:force_original_aspect_ratio=decrease")
        vf_parts.append("pad=1920:1080:(ow-iw)/2:(oh-ih)/2")

    vf = ",".join(vf_parts)

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-t", str(duration),
        "-i", str(video_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        logger.warning("FFmpeg clip extraction failed: %s", stderr.decode(errors="replace")[-500:])
        raise RuntimeError(f"FFmpeg exited with code {proc.returncode}")


async def _extract_thumbnail(video_path: Path, out_path: Path, time_sec: float) -> None:
    """Extract a single frame as a JPEG thumbnail."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-ss", str(time_sec),
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "3",
        str(out_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()


async def _upload_to_s3(file_path: Path, key: str, settings) -> str:
    """Upload a local file to S3 and return its public URL."""
    import boto3

    s3 = boto3.client(
        "s3",
        region_name=settings.s3_region if hasattr(settings, "s3_region") else "us-east-2",
        aws_access_key_id=getattr(settings, "aws_access_key_id", "") or os.environ.get("AWS_ACCESS_KEY_ID", ""),
        aws_secret_access_key=getattr(settings, "aws_secret_access_key", "") or os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
    )

    bucket = getattr(settings, "s3_bucket", "") or os.environ.get("S3_BUCKET", "")
    region = getattr(settings, "s3_region", "us-east-2") or "us-east-2"

    content_type = "video/mp4" if file_path.suffix == ".mp4" else "image/jpeg"

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: s3.upload_file(
        str(file_path), bucket, key,
        ExtraArgs={"ContentType": content_type, "CacheControl": "public, max-age=31536000"},
    ))

    url = f"https://{bucket}.s3.{region}.amazonaws.com/{key}"
    logger.info("Uploaded clip to %s", url)
    return url

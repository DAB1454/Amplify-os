"""Lyric video generator — creates short videos from image + audio + lyrics.

Uses FFmpeg to combine a static image (with subtle Ken Burns zoom) with
an audio snippet and animated lyrics overlay. Output is optimized for
Instagram Reels, TikTok, and YouTube Shorts.

When OpenAI API key is available, uses Whisper to transcribe the audio
clip for word-level timing instead of estimating from stored lyrics.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Resolution presets — 720p to stay within Render Starter memory limits
RESOLUTIONS: dict[str, tuple[int, int]] = {
    "9:16": (720, 1280),   # Reels, Shorts, TikTok
    "1:1": (720, 720),     # Feed posts
    "16:9": (1280, 720),   # YouTube landscape
}


async def generate_lyric_video(
    *,
    image_path: str,
    audio_path: str,
    lyrics: str,
    output_path: str,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 30,
    audio_start_seconds: int = 0,
    artist_name: str = "",
    track_title: str = "",
) -> str:
    """Generate a lyric video using FFmpeg.

    Parameters
    ----------
    audio_start_seconds : int
        Where to start the audio clip (e.g. 30 = skip to chorus).
        The lyrics passed in should already be the subset for this segment.

    If OPENAI_API_KEY is set, uses Whisper to transcribe the actual audio
    clip for word-level timing. Otherwise falls back to evenly-spaced lyrics.

    Returns the output file path.
    """
    width, height = RESOLUTIONS.get(aspect_ratio, (720, 1280))

    # First, extract the audio segment we'll use
    segment_audio_path = output_path.replace(".mp4", "-segment.wav")
    await _extract_audio_segment(audio_path, segment_audio_path, audio_start_seconds, duration_seconds)

    # Try Whisper transcription for word-level timing
    timed_lines: list[tuple[float, float, str]] | None = None
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        try:
            timed_lines = await _whisper_transcribe(segment_audio_path, openai_key)
            if timed_lines:
                logger.info("Whisper transcribed %d segments for lyric video", len(timed_lines))
        except Exception as exc:
            logger.warning("Whisper transcription failed, falling back to estimated timing: %s", exc)

    ass_path = output_path.replace(".mp4", ".ass")

    if timed_lines:
        # Use Whisper's word-level timing for perfect sync
        _write_ass_subtitles_timed(ass_path, timed_lines, duration_seconds, width, height, artist_name, track_title)
    else:
        # Fall back to evenly-spaced lyrics from stored text
        lines = []
        for l in lyrics.strip().split("\n"):
            stripped = l.strip()
            if not stripped:
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                continue
            if stripped.lower() in ("verse", "chorus", "bridge", "outro", "intro", "pre-chorus", "hook"):
                continue
            lines.append(stripped)

        if not lines:
            lines = [f"{track_title}" if track_title else ""]

        _write_ass_subtitles(ass_path, lines, duration_seconds, width, height, artist_name, track_title)

    # Build FFmpeg command
    fps = 24
    total_frames = duration_seconds * fps

    # Ken Burns: slow zoom from 100% to 108%
    zoom_filter = (
        f"zoompan=z='min(zoom+0.0003,1.08)':d={total_frames}"
        f":s={width}x{height}:fps={fps}"
    )

    # Audio fade in/out
    audio_fade_out_start = max(0, duration_seconds - 2)
    audio_filter = f"afade=t=in:d=1,afade=t=out:st={audio_fade_out_start}:d=2"

    cmd = [
        "ffmpeg", "-y",
        # Input 1: image (looped)
        "-loop", "1", "-i", image_path,
        # Input 2: pre-extracted audio segment (already trimmed to offset+duration)
        "-i", segment_audio_path,
        # Video filter: zoom + subtitles
        "-filter_complex",
        f"[0:v]{zoom_filter},ass={ass_path}[v]",
        "-map", "[v]",
        "-map", "1:a",
        # Duration
        "-t", str(duration_seconds),
        # Audio filter
        "-af", audio_filter,
        # Output settings — ultrafast for speed on constrained servers
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart",
        "-shortest",
        output_path,
    ]

    logger.info("Running FFmpeg: %s", " ".join(cmd))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

    if proc.returncode != 0:
        error_msg = stderr.decode()[-500:] if stderr else "unknown error"
        logger.error("FFmpeg failed (rc=%d): %s", proc.returncode, error_msg)
        raise RuntimeError(f"FFmpeg failed: {error_msg}")

    logger.info("Lyric video generated: %s", output_path)

    # Clean up temp segment file
    try:
        Path(segment_audio_path).unlink(missing_ok=True)
    except OSError:
        pass

    return output_path


async def _extract_audio_segment(
    input_path: str,
    output_path: str,
    start_seconds: int,
    duration_seconds: int,
) -> None:
    """Extract a segment of audio using FFmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_seconds),
        "-i", input_path,
        "-t", str(duration_seconds),
        "-c:a", "pcm_s16le",  # WAV format for Whisper compatibility
        "-ar", "16000",       # 16kHz mono for Whisper
        "-ac", "1",
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0:
        # Non-fatal — fall back to full audio
        logger.warning("Audio segment extraction failed (rc=%d), using full file", proc.returncode)
        import shutil
        shutil.copy2(input_path, output_path)


async def _whisper_transcribe(
    audio_path: str,
    api_key: str,
) -> list[tuple[float, float, str]] | None:
    """Transcribe audio using OpenAI Whisper API with word-level timestamps.

    Returns list of (start_sec, end_sec, text) tuples, or None on failure.
    Groups words into natural phrase chunks (~3-6 words) for subtitle display.
    """
    import httpx

    file_size = Path(audio_path).stat().st_size
    if file_size < 1000:  # Too small to transcribe
        return None

    async with httpx.AsyncClient(timeout=30) as client:
        with open(audio_path, "rb") as f:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                data={
                    "model": "whisper-1",
                    "response_format": "verbose_json",
                    "timestamp_granularity[]": "word",
                },
                files={"file": ("audio.wav", f, "audio/wav")},
            )

    if resp.status_code != 200:
        logger.warning("Whisper API returned %d: %s", resp.status_code, resp.text[:200])
        return None

    data = resp.json()
    words = data.get("words", [])
    if not words:
        # Fall back to segment-level timestamps
        segments = data.get("segments", [])
        if segments:
            return [(s["start"], s["end"], s["text"].strip()) for s in segments if s.get("text", "").strip()]
        return None

    # Group words into natural phrases (~3-6 words each)
    phrases: list[tuple[float, float, str]] = []
    current_words: list[str] = []
    phrase_start: float = 0.0
    phrase_end: float = 0.0

    for w in words:
        word_text = w.get("word", "").strip()
        if not word_text:
            continue

        if not current_words:
            phrase_start = w.get("start", 0.0)

        current_words.append(word_text)
        phrase_end = w.get("end", phrase_start + 1.0)

        # Break into a new phrase every 4-6 words or at natural pauses (>0.3s gap)
        should_break = (
            len(current_words) >= 5
            or (len(current_words) >= 3 and (phrase_end - phrase_start) > 2.5)
        )
        if should_break:
            phrases.append((phrase_start, phrase_end, " ".join(current_words)))
            current_words = []

    # Flush remaining words
    if current_words:
        phrases.append((phrase_start, phrase_end, " ".join(current_words)))

    return phrases if phrases else None


def _write_ass_subtitles_timed(
    path: str,
    timed_lines: list[tuple[float, float, str]],
    duration: int,
    width: int,
    height: int,
    artist_name: str = "",
    track_title: str = "",
) -> None:
    """Write ASS subtitle file using Whisper's word-level timestamps."""
    font_size = int(height * 0.04)
    title_font_size = int(height * 0.035)
    margin_bottom = int(height * 0.15)

    ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Lyrics,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,1,0,1,3,2,2,40,40,{margin_bottom},1
Style: Title,Arial,{title_font_size},&H00CCCCCC,&H000000FF,&H00000000,&H80000000,0,1,0,0,100,100,0,0,1,2,1,8,40,40,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Title card
    header_time = 3.0 if (artist_name or track_title) else 0.0
    if artist_name or track_title:
        title_text = ""
        if artist_name:
            title_text += artist_name
        if track_title:
            if title_text:
                title_text += " — "
            title_text += track_title
        ass += f"Dialogue: 0,{_format_ass_time(0)},{_format_ass_time(header_time)},Title,,0,0,0,,{{\\fad(500,500)}}{_escape_ass(title_text)}\n"

    # Timed lyric lines from Whisper
    for start, end, text in timed_lines:
        if not text.strip():
            continue
        # Clamp to video duration
        if start > duration:
            break
        end = min(end + 0.2, duration)  # Small extension for readability
        start_ts = _format_ass_time(start)
        end_ts = _format_ass_time(end)
        ass += f"Dialogue: 0,{start_ts},{end_ts},Lyrics,,0,0,0,,{{\\fad(200,200)}}{_escape_ass(text)}\n"

    with open(path, "w", encoding="utf-8") as f:
        f.write(ass)


def _write_ass_subtitles(
    path: str,
    lines: list[str],
    duration: int,
    width: int,
    height: int,
    artist_name: str = "",
    track_title: str = "",
) -> None:
    """Write an ASS subtitle file with timed, animated lyrics."""
    # Calculate time per line (with small overlap for transitions)
    header_time = 3.0 if (artist_name or track_title) else 0.0
    available_time = duration - header_time - 1.0  # 1s buffer at end
    time_per_line = max(1.5, available_time / max(len(lines), 1))

    # Font sizing based on resolution
    font_size = int(height * 0.04)  # ~4% of height
    title_font_size = int(height * 0.035)
    margin_bottom = int(height * 0.15)  # Position in lower portion

    # ASS header
    ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Lyrics,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,1,0,1,3,2,2,40,40,{margin_bottom},1
Style: Title,Arial,{title_font_size},&H00CCCCCC,&H000000FF,&H00000000,&H80000000,0,1,0,0,100,100,0,0,1,2,1,8,40,40,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Title card (artist + track name at top)
    if artist_name or track_title:
        title_text = ""
        if artist_name:
            title_text += artist_name
        if track_title:
            if title_text:
                title_text += " — "
            title_text += track_title
        start_ts = _format_ass_time(0)
        end_ts = _format_ass_time(header_time)
        ass += f"Dialogue: 0,{start_ts},{end_ts},Title,,0,0,0,,{{\\fad(500,500)}}{_escape_ass(title_text)}\n"

    # Lyric lines
    for i, line in enumerate(lines):
        start = header_time + i * time_per_line
        end = start + time_per_line + 0.3  # small overlap
        if end > duration:
            end = duration

        start_ts = _format_ass_time(start)
        end_ts = _format_ass_time(end)

        # Fade in/out effect
        fade_in = 300  # ms
        fade_out = 300  # ms
        ass += f"Dialogue: 0,{start_ts},{end_ts},Lyrics,,0,0,0,,{{\\fad({fade_in},{fade_out})}}{_escape_ass(line)}\n"

    with open(path, "w", encoding="utf-8") as f:
        f.write(ass)


def _format_ass_time(seconds: float) -> str:
    """Format seconds as ASS timestamp: H:MM:SS.CC"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_ass(text: str) -> str:
    """Escape special ASS characters."""
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


async def generate_static_video(
    *,
    image_path: str,
    audio_path: str,
    output_path: str,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 30,
    audio_start_seconds: int = 0,
) -> str:
    """Generate a simple video: static image (with Ken Burns zoom) + trimmed audio.

    No lyrics overlay — just image + audio combined into a proper video
    suitable for TikTok, Reels, or Shorts.

    Parameters
    ----------
    audio_start_seconds : int
        Where to start the audio clip from (e.g. 30 = skip first 30s to get to chorus).
    """
    width, height = RESOLUTIONS.get(aspect_ratio, (720, 1280))
    fps = 24  # 24fps is plenty for a static image + zoom
    total_frames = duration_seconds * fps

    # Ken Burns: slow zoom from 100% to 108%
    zoom_filter = (
        f"zoompan=z='min(zoom+0.0003,1.08)':d={total_frames}"
        f":s={width}x{height}:fps={fps}"
    )

    # Audio: trim to duration, fade in/out
    audio_fade_out_start = max(0, duration_seconds - 2)
    audio_filter = f"afade=t=in:d=1,afade=t=out:st={audio_fade_out_start}:d=2"

    cmd = [
        "ffmpeg", "-y",
        # Input 1: image (looped)
        "-loop", "1", "-i", image_path,
        # Input 2: audio (start from offset)
        "-ss", str(audio_start_seconds), "-i", audio_path,
        # Video filter: zoom
        "-vf", zoom_filter,
        # Duration
        "-t", str(duration_seconds),
        # Audio filter
        "-af", audio_filter,
        # Output settings — ultrafast preset for speed on constrained servers
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart",
        "-shortest",
        output_path,
    ]

    logger.info("Generating static video: %ds from image+audio", duration_seconds)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

    if proc.returncode != 0:
        error_msg = stderr.decode()[-500:] if stderr else "unknown error"
        logger.error("FFmpeg static video failed (rc=%d): %s", proc.returncode, error_msg)
        raise RuntimeError(f"FFmpeg failed: {error_msg}")

    logger.info("Static video generated: %s", output_path)
    return output_path


async def create_post_video(
    *,
    image_url: str,
    audio_url: str,
    tenant_id: uuid.UUID,
    media_service,  # MediaService instance
    aspect_ratio: str = "9:16",
    duration_seconds: int = 15,
    audio_start_seconds: int = 0,
) -> str:
    """End-to-end: download image + audio, generate video, upload to S3.

    Returns the uploaded video URL.
    """
    with tempfile.TemporaryDirectory(prefix="amplify-vid-") as tmpdir:
        # Determine file extensions from URLs
        img_ext = _ext_from_url(image_url, ".jpg")
        audio_ext = _ext_from_url(audio_url, ".wav")

        img_path = str(Path(tmpdir) / f"image{img_ext}")
        audio_path = str(Path(tmpdir) / f"audio{audio_ext}")
        output_path = str(Path(tmpdir) / f"output-{uuid.uuid4().hex[:8]}.mp4")

        # Download source files
        await download_url_to_file(image_url, img_path)
        await download_url_to_file(audio_url, audio_path)

        # Generate the video
        await generate_static_video(
            image_path=img_path,
            audio_path=audio_path,
            output_path=output_path,
            aspect_ratio=aspect_ratio,
            duration_seconds=duration_seconds,
            audio_start_seconds=audio_start_seconds,
        )

        # Upload to S3
        with open(output_path, "rb") as f:
            url = await media_service.upload(
                tenant_id,
                f,
                f"post-video-{uuid.uuid4().hex[:8]}.mp4",
                "video/mp4",
            )

        logger.info("Post video uploaded: %s", url)
        return url


def _ext_from_url(url: str, default: str = "") -> str:
    """Extract file extension from URL."""
    clean = url.split("?")[0].split("#")[0]
    name = clean.rstrip("/").rsplit("/", 1)[-1]
    if "." in name:
        return "." + name.rsplit(".", 1)[-1].lower()
    return default


async def download_url_to_file(url: str, dest: str) -> None:
    """Download a URL (S3 or HTTP) to a local file — streams to disk."""
    import httpx

    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    f.write(chunk)

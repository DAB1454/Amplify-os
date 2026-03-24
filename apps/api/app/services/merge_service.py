"""Audio/video merge service — combines a video file with an audio track using ffmpeg."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import BinaryIO

import httpx

logger = logging.getLogger(__name__)


async def download_file(url: str, suffix: str = "") -> Path:
    """Download a URL to a temp file."""
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        if not suffix:
            ct = resp.headers.get("content-type", "")
            if "mp4" in ct:
                suffix = ".mp4"
            elif "quicktime" in ct or "mov" in ct:
                suffix = ".mov"
            elif "webm" in ct:
                suffix = ".webm"
            elif "mpeg" in ct or "mp3" in ct:
                suffix = ".mp3"
            elif "wav" in ct:
                suffix = ".wav"
            elif "flac" in ct:
                suffix = ".flac"
            elif "m4a" in ct or "mp4" in ct:
                suffix = ".m4a"
            else:
                suffix = ".bin"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.write(resp.content)
        tmp.close()
        return Path(tmp.name)


async def merge_video_audio(
    video_url: str,
    audio_url: str,
    *,
    trim_to_video: bool = True,
) -> Path:
    """Download video + audio, merge with ffmpeg, return path to merged file.

    The merged file replaces the video's original audio with the provided audio track.
    If trim_to_video=True, the audio is trimmed to match the video duration.
    """
    video_path = await download_file(video_url, suffix=".mp4")
    audio_path = await download_file(audio_url)

    output_path = Path(tempfile.mktemp(suffix=".mp4"))

    try:
        # ffmpeg: take video from first input, audio from second input
        # -shortest: stop when the shorter stream ends
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(audio_path),
            "-c:v", "copy",          # don't re-encode video
            "-c:a", "aac",           # encode audio as AAC (universal compat)
            "-b:a", "192k",
            "-map", "0:v:0",         # video from first input
            "-map", "1:a:0",         # audio from second input
        ]
        if trim_to_video:
            cmd.append("-shortest")
        cmd.append(str(output_path))

        logger.info("Running ffmpeg merge: video=%s audio=%s", video_path.name, audio_path.name)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode()[-500:] if stderr else "unknown error"
            raise RuntimeError(f"ffmpeg merge failed (exit {process.returncode}): {error_msg}")

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("ffmpeg produced empty output")

        logger.info("Merge complete: %s (%d bytes)", output_path.name, output_path.stat().st_size)
        return output_path

    finally:
        # Clean up input temp files
        video_path.unlink(missing_ok=True)
        audio_path.unlink(missing_ok=True)

"""Replicate AI video generation service.

Generates short video clips from text prompts using Replicate's API,
then stitches them with audio via FFmpeg for music content.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Default model — Stability AI's Stable Video Diffusion via Replicate
# Can be swapped to runway, kling, etc. by changing this
DEFAULT_MODEL = "stability-ai/stable-video-diffusion:3f0457e4619daac51203dedb472816fd4af51f3149fa7a9e0b5ffcf1b8172438"

# Cost estimate per generation (for metering/display)
ESTIMATED_COST_PER_CLIP = 0.25  # USD


async def generate_video_from_prompt(
    *,
    prompt: str,
    image_url: str | None = None,
    duration_seconds: int = 4,
    aspect_ratio: str = "9:16",
    replicate_api_token: str = "",
) -> str:
    """Generate a short video clip from a text prompt via Replicate.

    Returns the URL of the generated video.
    """
    if not replicate_api_token:
        replicate_api_token = os.environ.get("REPLICATE_API_TOKEN", "")
    if not replicate_api_token:
        raise ValueError("REPLICATE_API_TOKEN not configured")

    headers = {
        "Authorization": f"Bearer {replicate_api_token}",
        "Content-Type": "application/json",
        "Prefer": "wait",  # Use sync mode for shorter waits
    }

    # Use image-to-video if we have an image, text-to-video otherwise
    if image_url:
        # Stable Video Diffusion: image → video (versioned model)
        model = DEFAULT_MODEL
        model_input = {
            "input_image": image_url,
            "motion_bucket_id": 127,
            "fps": 24,
            "cond_aug": 0.02,
        }
        api_url = "https://api.replicate.com/v1/predictions"
        body = {
            "version": model.split(":")[-1],
            "input": model_input,
        }
    else:
        # Text-to-video: minimax/video-01-live (official model — uses models endpoint)
        model = "minimax/video-01-live"
        model_input = {
            "prompt": prompt,
            "prompt_optimizer": True,
        }
        # Official models use /v1/models/{owner}/{name}/predictions
        api_url = f"https://api.replicate.com/v1/models/{model}/predictions"
        body = {
            "input": model_input,
        }

    logger.info("Replicate generation: model=%s url=%s prompt=%s", model, api_url, prompt[:100])

    # Create prediction
    async with httpx.AsyncClient(timeout=120) as client:
        create_resp = await client.post(api_url, headers=headers, json=body)
        if create_resp.status_code >= 400:
            logger.error(
                "Replicate API error %d: %s", create_resp.status_code, create_resp.text[:500]
            )
            create_resp.raise_for_status()
        prediction = create_resp.json()
        prediction_id = prediction["id"]
        status = prediction.get("status")
        logger.info("Replicate prediction created: %s (status=%s)", prediction_id, status)

        # If sync mode returned completed result, extract output directly
        if status == "succeeded":
            output = prediction.get("output")
            if isinstance(output, list):
                return output[0]
            if isinstance(output, str):
                return output

    # Poll for completion (max 5 minutes)
    video_url = await _poll_prediction(prediction_id, replicate_api_token, timeout=300)
    return video_url


def parse_timed_scenes(prompt: str) -> list[dict]:
    """Parse timed shot descriptions from a user prompt.

    Recognizes patterns like:
        Shot #1 (0:14-0:21): description...
        Shot #1 (0:14-0:21 of audio): description...
        Shot#2: (0:21-0:27) description...

    Returns list of {"prompt": str, "start": float, "end": float, "duration": float}
    or empty list if no timing found.
    """
    import re

    # Step 1: Find all timestamp markers with their positions
    # Matches (M:SS-M:SS) with optional trailing text like "of audio" before the closing paren
    ts_pattern = re.compile(
        r'\((\d+):(\d+)\s*[-–—]\s*(\d+):(\d+)(?:\s+[^)]+)?\)',
        re.IGNORECASE,
    )

    # Step 2: Split prompt into shot blocks by looking for "Shot #N" markers
    shot_split = re.compile(r'(?=shot\s*#?\s*\d)', re.IGNORECASE)
    blocks = shot_split.split(prompt)

    scenes = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # Find timestamp in this block
        ts_match = ts_pattern.search(block)
        if not ts_match:
            continue

        start_sec = int(ts_match.group(1)) * 60 + int(ts_match.group(2))
        end_sec = int(ts_match.group(3)) * 60 + int(ts_match.group(4))

        if end_sec <= start_sec:
            continue

        # Description is everything after the timestamp marker
        desc = block[ts_match.end():].strip()
        # Clean leading colons/whitespace
        desc = re.sub(r'^[:\s]+', '', desc).strip()

        if desc:
            scenes.append({
                "prompt": desc,
                "start": float(start_sec),
                "end": float(end_sec),
                "duration": float(end_sec - start_sec),
            })

    return scenes


async def generate_music_video(
    *,
    prompts: list[str],
    audio_url: str,
    image_url: str | None = None,
    duration_seconds: int = 30,
    aspect_ratio: str = "9:16",
    replicate_api_token: str = "",
    output_dir: str = "",
    scene_durations: list[float] | None = None,
    audio_start: float | None = None,
    audio_end: float | None = None,
) -> str:
    """Generate a multi-clip music video from scene prompts + audio.

    1. Generates one video clip per prompt via Replicate
    2. Downloads all clips
    3. Scales each clip to its target duration (if scene_durations provided)
    4. Stitches them with the audio track via FFmpeg
    5. Returns path to the final video

    If scene_durations is provided, each clip is time-stretched to match.
    If audio_start/audio_end are provided, audio is trimmed to that range.
    """
    from app.services.video_generator import download_url_to_file

    if not output_dir:
        import tempfile
        output_dir = tempfile.mkdtemp(prefix="musicvid_")

    output_path = str(Path(output_dir) / "final.mp4")
    clip_paths: list[str] = []

    # Generate clips (sequential to stay within rate limits)
    for i, prompt in enumerate(prompts):
        target_dur = scene_durations[i] if scene_durations and i < len(scene_durations) else None
        logger.info(
            "Generating clip %d/%d (target=%.1fs): %s",
            i + 1, len(prompts), target_dur or 4.0, prompt[:80],
        )
        try:
            clip_url = await generate_video_from_prompt(
                prompt=prompt,
                image_url=image_url if i == 0 else None,
                duration_seconds=4,
                aspect_ratio=aspect_ratio,
                replicate_api_token=replicate_api_token,
            )

            # Download clip
            raw_clip_path = str(Path(output_dir) / f"clip_{i:02d}_raw.mp4")
            await download_url_to_file(clip_url, raw_clip_path)

            # Scale clip to target duration if specified
            if target_dur and target_dur > 0:
                scaled_clip_path = str(Path(output_dir) / f"clip_{i:02d}.mp4")
                await _scale_clip_to_duration(raw_clip_path, scaled_clip_path, target_dur)
                clip_paths.append(scaled_clip_path)
            else:
                clip_paths.append(raw_clip_path)
        except Exception as exc:
            logger.warning("Clip %d generation failed: %s", i, exc)
            continue

    if not clip_paths:
        raise RuntimeError("No video clips were generated successfully")

    # Download audio
    audio_path = str(Path(output_dir) / "audio_input.mp3")
    await download_url_to_file(audio_url, audio_path)

    # Trim audio to range if specified
    trimmed_audio_path = audio_path
    if audio_start is not None or audio_end is not None:
        trimmed_audio_path = str(Path(output_dir) / "audio_trimmed.mp3")
        await _trim_audio(audio_path, trimmed_audio_path, audio_start or 0, audio_end)

    # Stitch clips with audio via FFmpeg
    await _stitch_clips_with_audio(
        clip_paths=clip_paths,
        audio_path=trimmed_audio_path,
        output_path=output_path,
        duration_seconds=duration_seconds,
    )

    return output_path


async def generate_scene_prompts_from_lyrics(
    lyrics: str,
    artist_name: str = "",
    track_title: str = "",
    num_scenes: int = 6,
) -> list[str]:
    """Use a simple heuristic to turn lyrics into video scene prompts.

    For now, splits lyrics into chunks and wraps each in a cinematic prompt.
    In the future, this could use an LLM for richer scene descriptions.
    """
    lines = [l.strip() for l in lyrics.strip().split("\n") if l.strip()]
    if not lines:
        return [f"Cinematic music video scene for {artist_name} - {track_title}"]

    # Group lines into scenes
    lines_per_scene = max(1, len(lines) // num_scenes)
    scenes: list[str] = []

    for i in range(0, len(lines), lines_per_scene):
        chunk = " / ".join(lines[i:i + lines_per_scene])
        # Create a cinematic prompt from the lyric chunk
        prompt = (
            f"Cinematic music video scene. Mood evoked by: \"{chunk}\". "
            f"Beautiful cinematography, shallow depth of field, "
            f"atmospheric lighting, country music aesthetic. "
            f"No text or words visible in the video."
        )
        scenes.append(prompt)

        if len(scenes) >= num_scenes:
            break

    return scenes


async def _poll_prediction(
    prediction_id: str,
    api_token: str,
    timeout: int = 300,
) -> str:
    """Poll Replicate for prediction completion."""
    import time
    start = time.time()

    async with httpx.AsyncClient(timeout=30) as client:
        while time.time() - start < timeout:
            resp = await client.get(
                f"https://api.replicate.com/v1/predictions/{prediction_id}",
                headers={"Authorization": f"Bearer {api_token}"},
            )
            resp.raise_for_status()
            data = resp.json()

            status = data.get("status")
            if status == "succeeded":
                output = data.get("output")
                if isinstance(output, list):
                    return output[0]  # First output URL
                if isinstance(output, str):
                    return output
                raise RuntimeError(f"Unexpected output format: {output}")
            elif status == "failed":
                error = data.get("error", "Unknown error")
                raise RuntimeError(f"Replicate prediction failed: {error}")
            elif status == "canceled":
                raise RuntimeError("Replicate prediction was canceled")

            # Wait before polling again
            await asyncio.sleep(3)

    raise TimeoutError(f"Replicate prediction {prediction_id} timed out after {timeout}s")


async def _scale_clip_to_duration(
    input_path: str,
    output_path: str,
    target_duration: float,
) -> None:
    """Scale a video clip to a target duration using FFmpeg.

    Uses setpts filter to time-stretch/compress the video.
    If the raw clip is ~4s and target is 7s, it slows down.
    If target is 3s, it speeds up.
    """
    # Probe the actual clip duration
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        input_path,
    ]
    probe_proc = await asyncio.create_subprocess_exec(
        *probe_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    probe_out, _ = await asyncio.wait_for(probe_proc.communicate(), timeout=15)
    try:
        actual_duration = float(probe_out.decode().strip())
    except (ValueError, AttributeError):
        actual_duration = 4.0  # fallback assumption

    if actual_duration <= 0:
        actual_duration = 4.0

    # Calculate speed factor: PTS multiplier (< 1 = slower, > 1 = faster)
    speed_factor = actual_duration / target_duration

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-filter:v", f"setpts={1/speed_factor:.4f}*PTS",
        "-an",  # drop audio from clip (we add the real audio track later)
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        output_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

    if proc.returncode != 0:
        error_msg = stderr.decode()[-500:] if stderr else "unknown error"
        logger.warning("Clip scaling failed (using raw): %s", error_msg)
        # Fall back to raw clip
        import shutil
        shutil.copy2(input_path, output_path)


async def _trim_audio(
    input_path: str,
    output_path: str,
    start_seconds: float,
    end_seconds: float | None = None,
) -> None:
    """Trim an audio file to a specific time range using FFmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-ss", str(start_seconds),
    ]
    if end_seconds is not None:
        duration = end_seconds - start_seconds
        cmd.extend(["-t", str(duration)])

    cmd.extend([
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

    if proc.returncode != 0:
        error_msg = stderr.decode()[-500:] if stderr else "unknown error"
        raise RuntimeError(f"Audio trim failed: {error_msg}")

    logger.info("Trimmed audio: %.1fs-%.1fs → %s", start_seconds, end_seconds or 0, output_path)


async def _stitch_clips_with_audio(
    *,
    clip_paths: list[str],
    audio_path: str,
    output_path: str,
    duration_seconds: int = 30,
) -> None:
    """Stitch video clips together with audio using FFmpeg."""
    # Create concat file
    concat_dir = str(Path(output_path).parent)
    concat_file = str(Path(concat_dir) / "concat.txt")
    with open(concat_file, "w") as f:
        for clip in clip_paths:
            f.write(f"file '{clip}'\n")

    audio_fade_out = max(0, duration_seconds - 2)

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", concat_file,
        "-i", audio_path,
        "-t", str(duration_seconds),
        "-af", f"afade=t=in:d=1,afade=t=out:st={audio_fade_out}:d=2",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-shortest",
        output_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

    if proc.returncode != 0:
        error_msg = stderr.decode()[-500:] if stderr else "unknown error"
        raise RuntimeError(f"FFmpeg stitch failed: {error_msg}")


async def generate_ai_video_for_post(
    *,
    image_url: str | None,
    audio_url: str,
    track_title: str = "",
    artist_name: str = "",
    lyrics: str = "",
    tenant_id: "uuid.UUID",
    replicate_api_token: str,
    s3_bucket: str,
    s3_region: str,
    aws_access_key_id: str,
    aws_secret_access_key: str,
    media_base_url: str = "",
    num_scenes: int = 4,
    duration_seconds: int = 15,
    aspect_ratio: str = "9:16",
    scene_durations: list[float] | None = None,
    audio_start: float | None = None,
    audio_end: float | None = None,
) -> tuple[str, float]:
    """Generate an AI video for a post via Replicate and upload to S3.

    Returns (video_url, cost_usd).
    """
    import tempfile

    # Build prompts
    if lyrics:
        prompts = await generate_scene_prompts_from_lyrics(
            lyrics=lyrics,
            artist_name=artist_name,
            track_title=track_title,
            num_scenes=num_scenes,
        )
    else:
        prompts = [
            f"Cinematic music video scene for {artist_name} - {track_title}. "
            f"Beautiful cinematography, atmospheric lighting."
        ] * num_scenes

    cost = len(prompts) * ESTIMATED_COST_PER_CLIP

    with tempfile.TemporaryDirectory(prefix="aivid_") as tmp_dir:
        output_path = await generate_music_video(
            prompts=prompts,
            audio_url=audio_url,
            image_url=image_url,
            duration_seconds=duration_seconds,
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

    logger.info("AI video generated: %s (cost=$%.2f, %d clips)", video_url, cost, len(prompts))
    return video_url, cost

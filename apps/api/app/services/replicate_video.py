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

    # Use image-to-video if we have an image, text-to-video otherwise
    if image_url:
        # Stable Video Diffusion: image → video
        model_input = {
            "input_image": image_url,
            "motion_bucket_id": 127,  # Amount of motion (0-255)
            "fps": 24,
            "cond_aug": 0.02,
        }
        model = DEFAULT_MODEL
    else:
        # Text-to-video model
        model = "minimax/video-01-live"
        model_input = {
            "prompt": prompt,
            "prompt_optimizer": True,
        }

    logger.info("Replicate generation: model=%s prompt=%s", model, prompt[:100])

    # Create prediction
    async with httpx.AsyncClient(timeout=120) as client:
        create_resp = await client.post(
            "https://api.replicate.com/v1/predictions",
            headers={
                "Authorization": f"Bearer {replicate_api_token}",
                "Content-Type": "application/json",
            },
            json={
                "version": model.split(":")[-1] if ":" in model else None,
                "model": model.split(":")[0] if ":" not in model else None,
                "input": model_input,
            },
        )
        create_resp.raise_for_status()
        prediction = create_resp.json()
        prediction_id = prediction["id"]
        logger.info("Replicate prediction created: %s", prediction_id)

    # Poll for completion (max 5 minutes)
    video_url = await _poll_prediction(prediction_id, replicate_api_token, timeout=300)
    return video_url


async def generate_music_video(
    *,
    prompts: list[str],
    audio_url: str,
    image_url: str | None = None,
    duration_seconds: int = 30,
    aspect_ratio: str = "9:16",
    replicate_api_token: str = "",
    output_dir: str = "",
) -> str:
    """Generate a multi-clip music video from scene prompts + audio.

    1. Generates one video clip per prompt via Replicate
    2. Downloads all clips
    3. Stitches them with the audio track via FFmpeg
    4. Returns path to the final video

    Each clip is ~4 seconds, so 6-8 prompts ≈ 30 seconds.
    """
    from app.services.video_generator import download_url_to_file

    if not output_dir:
        import tempfile
        output_dir = tempfile.mkdtemp(prefix="musicvid_")

    output_path = str(Path(output_dir) / "final.mp4")
    clip_paths: list[str] = []

    # Generate clips (sequential to stay within rate limits)
    for i, prompt in enumerate(prompts):
        logger.info("Generating clip %d/%d: %s", i + 1, len(prompts), prompt[:80])
        try:
            clip_url = await generate_video_from_prompt(
                prompt=prompt,
                image_url=image_url if i == 0 else None,  # Use image for first clip
                duration_seconds=4,
                aspect_ratio=aspect_ratio,
                replicate_api_token=replicate_api_token,
            )

            # Download clip
            clip_path = str(Path(output_dir) / f"clip_{i:02d}.mp4")
            await download_url_to_file(clip_url, clip_path)
            clip_paths.append(clip_path)
        except Exception as exc:
            logger.warning("Clip %d generation failed: %s", i, exc)
            continue

    if not clip_paths:
        raise RuntimeError("No video clips were generated successfully")

    # Download audio
    audio_path = str(Path(output_dir) / "audio_input.mp3")
    await download_url_to_file(audio_url, audio_path)

    # Stitch clips with audio via FFmpeg
    await _stitch_clips_with_audio(
        clip_paths=clip_paths,
        audio_path=audio_path,
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

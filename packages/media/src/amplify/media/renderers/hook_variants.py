"""Generate hook variant clips from a track using ffmpeg."""

from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path

from amplify.media.models import (
    AssetManifest,
    RenderSpec,
    RenderType,
    RenderedAsset,
)

logger = logging.getLogger(__name__)


class HookVariantGenerator:
    """Cuts multiple short clips from different hook points in a track.

    Requires ffmpeg to be installed and available on PATH.
    """

    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        self.ffmpeg_path = ffmpeg_path

    async def generate(
        self,
        spec: RenderSpec,
        output_dir: Path,
    ) -> AssetManifest:
        """Generate hook variant clips.

        Args:
            spec: RenderSpec with audio_path, hook_timestamps, hook_count.
            output_dir: Directory to write the output clips.

        Returns:
            AssetManifest with one RenderedAsset per clip.
        """
        manifest = AssetManifest(
            render_type=RenderType.HOOK_VARIANT,
            aspect_ratio=spec.aspect_ratio,
        )

        if not spec.audio_path:
            manifest.errors.append("audio_path is required")
            return manifest

        audio = Path(spec.audio_path)
        if not audio.exists():
            manifest.errors.append(f"Audio not found: {audio}")
            return manifest

        if not spec.hook_timestamps:
            manifest.errors.append("hook_timestamps is required (list of (start, end) tuples)")
            return manifest

        output_dir.mkdir(parents=True, exist_ok=True)

        # Limit to hook_count
        timestamps = spec.hook_timestamps[:spec.hook_count]

        for i, (start, end) in enumerate(timestamps):
            duration = end - start
            if duration <= 0:
                manifest.errors.append(f"Hook {i}: invalid duration (start={start}, end={end})")
                continue

            variant_label = chr(ord("A") + i)  # A, B, C...
            out_file = output_dir / f"hook_{variant_label}_{audio.stem}.wav"

            cmd = [
                self.ffmpeg_path, "-y",
                "-ss", str(start),
                "-t", str(duration),
                "-i", str(audio),
                "-acodec", "pcm_s16le",
                str(out_file),
            ]

            logger.info("HookVariant %s cmd: %s", variant_label, " ".join(cmd))
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
            except subprocess.CalledProcessError as e:
                manifest.errors.append(f"Hook {variant_label} ffmpeg failed: {e.stderr[:300]}")
                continue
            except FileNotFoundError:
                manifest.errors.append("ffmpeg not found on PATH")
                return manifest

            stat = out_file.stat()
            sha = hashlib.sha256(out_file.read_bytes()).hexdigest()

            manifest.assets.append(RenderedAsset(
                path=str(out_file),
                filename=out_file.name,
                format="wav",
                duration_seconds=duration,
                file_size_bytes=stat.st_size,
                checksum_sha256=sha,
            ))

        return manifest

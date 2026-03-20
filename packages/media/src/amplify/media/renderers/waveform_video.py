"""Generate waveform visualization videos with album art background."""

from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path

from amplify.media.models import (
    ASPECT_DIMENSIONS,
    AssetManifest,
    RenderSpec,
    RenderType,
    RenderedAsset,
)

logger = logging.getLogger(__name__)


class WaveformRenderer:
    """Creates a video with album art background and animated audio waveform.

    Uses ffmpeg's showwaves filter overlaid on a scaled background image.
    Requires ffmpeg to be installed and available on PATH.
    """

    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        self.ffmpeg_path = ffmpeg_path

    async def render(
        self,
        spec: RenderSpec,
        output_dir: Path,
    ) -> AssetManifest:
        """Render a waveform video.

        Args:
            spec: RenderSpec with audio_path, artwork_path, aspect_ratio.
            output_dir: Directory to write the output file.

        Returns:
            AssetManifest describing the rendered output.
        """
        manifest = AssetManifest(
            render_type=RenderType.WAVEFORM,
            aspect_ratio=spec.aspect_ratio,
        )

        if not spec.audio_path:
            manifest.errors.append("audio_path is required")
            return manifest
        if not spec.artwork_path:
            manifest.errors.append("artwork_path is required")
            return manifest

        audio = Path(spec.audio_path)
        artwork = Path(spec.artwork_path)

        if not audio.exists():
            manifest.errors.append(f"Audio not found: {audio}")
            return manifest
        if not artwork.exists():
            manifest.errors.append(f"Artwork not found: {artwork}")
            return manifest

        w, h = ASPECT_DIMENSIONS[spec.aspect_ratio]
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"waveform_{audio.stem}.mp4"

        # Waveform height = 20% of video height, centered at bottom third
        wave_h = h // 5
        wave_y = h - wave_h - (h // 10)

        # Complex filtergraph:
        # [0:v] scale artwork to fill -> [bg]
        # [1:a] generate waveform overlay -> [wave]
        # [bg][wave] overlay at position -> output
        filter_complex = (
            f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1[bg];"
            f"[1:a]showwaves=s={w}x{wave_h}:mode=cline:colors=white@0.7:rate=25[wave];"
            f"[bg][wave]overlay=0:{wave_y}:shortest=1[out]"
        )

        cmd = [
            self.ffmpeg_path, "-y",
            "-loop", "1", "-i", str(artwork),
            "-i", str(audio),
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-map", "1:a",
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            str(out_file),
        ]

        logger.info("WaveformRenderer cmd: %s", " ".join(cmd))
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
        except subprocess.CalledProcessError as e:
            manifest.errors.append(f"ffmpeg failed: {e.stderr[:500]}")
            return manifest
        except FileNotFoundError:
            manifest.errors.append("ffmpeg not found on PATH")
            return manifest

        stat = out_file.stat()
        sha = hashlib.sha256(out_file.read_bytes()).hexdigest()

        manifest.assets.append(RenderedAsset(
            path=str(out_file),
            filename=out_file.name,
            format="mp4",
            width=w,
            height=h,
            file_size_bytes=stat.st_size,
            checksum_sha256=sha,
        ))
        return manifest

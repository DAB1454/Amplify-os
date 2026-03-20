"""Generate teaser videos — short clips with text overlay on artwork + audio."""

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


class TeaserRenderer:
    """Creates a teaser video: album art background + audio snippet + text overlay.

    Useful for pre-release "coming soon" posts. Requires ffmpeg.
    """

    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        self.ffmpeg_path = ffmpeg_path

    async def render(
        self,
        spec: RenderSpec,
        output_dir: Path,
    ) -> AssetManifest:
        """Render a teaser video.

        Args:
            spec: RenderSpec with audio_path, artwork_path, title, subtitle, aspect_ratio.
            output_dir: Directory to write the output file.

        Returns:
            AssetManifest describing the rendered output.
        """
        manifest = AssetManifest(
            render_type=RenderType.TEASER,
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
        out_file = output_dir / f"teaser_{audio.stem}.mp4"

        # Build filter: scale artwork, add dark overlay, add title + subtitle text
        title_escaped = (spec.title or "").replace("'", "'\\''").replace(":", "\\:")
        sub_escaped = (spec.subtitle or "").replace("'", "'\\''").replace(":", "\\:")

        drawtext_parts = []
        if title_escaped:
            drawtext_parts.append(
                f"drawtext=text='{title_escaped}'"
                f":fontsize={min(72, w // 15)}"
                f":fontcolor=white:borderw=3:bordercolor=black"
                f":x=(w-text_w)/2:y=(h/2-text_h)"
            )
        if sub_escaped:
            drawtext_parts.append(
                f"drawtext=text='{sub_escaped}'"
                f":fontsize={min(48, w // 22)}"
                f":fontcolor=white@0.85:borderw=2:bordercolor=black"
                f":x=(w-text_w)/2:y=(h/2+20)"
            )

        vf_parts = [
            f"scale={w}:{h}:force_original_aspect_ratio=increase",
            f"crop={w}:{h}",
            "setsar=1",
            # Dark overlay via curves
            "curves=all='0/0 0.5/0.3 1/0.6'",
        ]
        vf_parts.extend(drawtext_parts)
        vf = ",".join(vf_parts)

        cmd = [
            self.ffmpeg_path, "-y",
            "-loop", "1", "-i", str(artwork),
            "-i", str(audio),
            "-vf", vf,
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            str(out_file),
        ]

        logger.info("TeaserRenderer cmd: %s", " ".join(cmd))
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
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

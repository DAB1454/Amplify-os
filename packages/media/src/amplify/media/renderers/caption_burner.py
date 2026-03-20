"""Burn text captions onto video frames using ffmpeg drawtext."""

from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path

from amplify.media.models import (
    ASPECT_DIMENSIONS,
    AssetManifest,
    Caption,
    CaptionStyle,
    RenderSpec,
    RenderType,
    RenderedAsset,
)

logger = logging.getLogger(__name__)


def _escape_drawtext(text: str) -> str:
    """Escape special characters for ffmpeg drawtext filter."""
    return text.replace("\\", "\\\\").replace("'", "'\\''").replace(":", "\\:")


def _position_y(style: CaptionStyle, height: int) -> str:
    if style.position == "top":
        return "40"
    if style.position == "center":
        return f"({height}/2 - text_h/2)"
    # bottom
    return f"({height} - text_h - {style.margin_bottom})"


def _build_drawtext_filters(
    captions: list[Caption],
    style: CaptionStyle,
    height: int,
) -> list[str]:
    """Build a list of drawtext filter strings for ffmpeg."""
    filters = []
    y_expr = _position_y(style, height)

    for cap in captions:
        escaped = _escape_drawtext(cap.text)
        f = (
            f"drawtext=text='{escaped}'"
            f":fontfile=''"
            f":fontsize={style.font_size}"
            f":fontcolor={style.font_color}"
            f":borderw={style.outline_width}"
            f":bordercolor={style.outline_color}"
            f":x=(w-text_w)/2"
            f":y={y_expr}"
            f":enable='between(t,{cap.start},{cap.end})'"
        )
        filters.append(f)
    return filters


class CaptionBurner:
    """Burns text captions onto video frames using ffmpeg.

    Requires ffmpeg to be installed and available on PATH.
    """

    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        self.ffmpeg_path = ffmpeg_path

    async def render(
        self,
        spec: RenderSpec,
        output_dir: Path,
    ) -> AssetManifest:
        """Render captions onto a video.

        Args:
            spec: RenderSpec with video_path, captions, caption_style, aspect_ratio.
            output_dir: Directory to write the output file.

        Returns:
            AssetManifest describing the rendered output.
        """
        manifest = AssetManifest(
            render_type=RenderType.CAPTION_BURN,
            aspect_ratio=spec.aspect_ratio,
            source_spec=spec.model_dump(exclude={"captions"}),
        )

        if not spec.video_path:
            manifest.errors.append("video_path is required")
            return manifest

        video = Path(spec.video_path)
        if not video.exists():
            manifest.errors.append(f"Video not found: {video}")
            return manifest

        if not spec.captions:
            manifest.errors.append("No captions provided")
            return manifest

        w, h = ASPECT_DIMENSIONS[spec.aspect_ratio]
        output_dir.mkdir(parents=True, exist_ok=True)
        out_file = output_dir / f"captioned_{video.stem}.mp4"

        # Build filter chain: scale + drawtext
        scale_filter = f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
        drawtext_filters = _build_drawtext_filters(spec.captions, spec.caption_style, h)
        vf = ",".join([scale_filter] + drawtext_filters)

        cmd = [
            self.ffmpeg_path, "-y",
            "-i", str(video),
            "-vf", vf,
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(out_file),
        ]

        logger.info("CaptionBurner cmd: %s", " ".join(cmd))
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

"""Generate lyric card images using Pillow."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from amplify.media.models import (
    ASPECT_DIMENSIONS,
    AssetManifest,
    RenderSpec,
    RenderType,
    RenderedAsset,
)

logger = logging.getLogger(__name__)


def _hex_to_rgba(hex_color: str) -> tuple[int, ...]:
    """Convert hex color (#RRGGBB or #RRGGBBAA) to RGBA tuple."""
    h = hex_color.lstrip("#")
    if len(h) == 6:
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4)) + (255,)
    if len(h) == 8:
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4, 6))
    return (255, 255, 255, 255)


def _load_font(name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Try loading a TrueType font, fall back to default."""
    try:
        return ImageFont.truetype(name, size)
    except (OSError, IOError):
        try:
            return ImageFont.truetype("arial.ttf", size)
        except (OSError, IOError):
            return ImageFont.load_default()


def _wrap_text(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """Word-wrap text to fit within max_width pixels."""
    words = text.split()
    lines: list[str] = []
    current = ""

    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word

    if current:
        lines.append(current)
    return lines or [""]


class LyricCardRenderer:
    """Generates static image cards with lyrics overlaid on artwork.

    Uses Pillow for image composition and text rendering.
    """

    def __init__(self) -> None:
        pass

    async def render(
        self,
        spec: RenderSpec,
        output_dir: Path,
    ) -> AssetManifest:
        """Render a lyric card image.

        Args:
            spec: RenderSpec with lyrics, artwork_path, colors, aspect_ratio, title, subtitle.
            output_dir: Directory to write the output file.

        Returns:
            AssetManifest describing the rendered output.
        """
        manifest = AssetManifest(
            render_type=RenderType.LYRIC_CARD,
            aspect_ratio=spec.aspect_ratio,
        )

        if not spec.lyrics:
            manifest.errors.append("lyrics text is required")
            return manifest

        w, h = ASPECT_DIMENSIONS[spec.aspect_ratio]
        output_dir.mkdir(parents=True, exist_ok=True)

        # Load or create background
        if spec.artwork_path and Path(spec.artwork_path).exists():
            bg = Image.open(spec.artwork_path).convert("RGBA")
            bg = bg.resize((w, h), Image.LANCZOS)
        else:
            bg = Image.new("RGBA", (w, h), (30, 30, 30, 255))

        # Dark overlay for readability
        overlay_color = _hex_to_rgba(spec.colors.get("overlay", "#00000099"))
        overlay = Image.new("RGBA", (w, h), overlay_color)
        bg = Image.alpha_composite(bg, overlay)

        draw = ImageDraw.Draw(bg)
        text_color = _hex_to_rgba(spec.colors.get("text", "#FFFFFF"))
        shadow_color = _hex_to_rgba(spec.colors.get("shadow", "#00000080"))

        # Title at top (if provided)
        margin = w // 10
        usable_width = w - 2 * margin
        y_cursor = h // 8

        if spec.title:
            title_font = _load_font(spec.caption_style.font, spec.caption_style.font_size + 8)
            title_lines = _wrap_text(spec.title, title_font, usable_width, draw)
            for line in title_lines:
                bbox = draw.textbbox((0, 0), line, font=title_font)
                tw = bbox[2] - bbox[0]
                x = (w - tw) // 2
                draw.text((x + 2, y_cursor + 2), line, fill=shadow_color, font=title_font)
                draw.text((x, y_cursor), line, fill=text_color, font=title_font)
                y_cursor += bbox[3] - bbox[1] + 10
            y_cursor += 20

        if spec.subtitle:
            sub_font = _load_font(spec.caption_style.font, spec.caption_style.font_size - 8)
            bbox = draw.textbbox((0, 0), spec.subtitle, font=sub_font)
            sw = bbox[2] - bbox[0]
            x = (w - sw) // 2
            draw.text((x + 1, y_cursor + 1), spec.subtitle, fill=shadow_color, font=sub_font)
            draw.text((x, y_cursor), spec.subtitle, fill=text_color, font=sub_font)
            y_cursor += bbox[3] - bbox[1] + 30

        # Lyrics centered vertically in remaining space
        lyric_font = _load_font(spec.caption_style.font, spec.caption_style.font_size)
        lyric_lines = _wrap_text(spec.lyrics, lyric_font, usable_width, draw)

        # Calculate total text height
        line_heights = []
        for line in lyric_lines:
            bbox = draw.textbbox((0, 0), line, font=lyric_font)
            line_heights.append(bbox[3] - bbox[1])

        total_text_h = sum(line_heights) + (len(lyric_lines) - 1) * 12
        remaining_space = h - y_cursor - (h // 8)
        lyric_y = y_cursor + max(0, (remaining_space - total_text_h) // 2)

        for i, line in enumerate(lyric_lines):
            bbox = draw.textbbox((0, 0), line, font=lyric_font)
            lw = bbox[2] - bbox[0]
            x = (w - lw) // 2
            draw.text((x + 2, lyric_y + 2), line, fill=shadow_color, font=lyric_font)
            draw.text((x, lyric_y), line, fill=text_color, font=lyric_font)
            lyric_y += line_heights[i] + 12

        # Save
        out_name = f"lyric_card_{spec.title or 'untitled'}".replace(" ", "_")[:60]
        fmt = spec.output_format if spec.output_format in ("png", "jpg") else "png"
        out_file = output_dir / f"{out_name}.{fmt}"

        if fmt == "jpg":
            bg = bg.convert("RGB")
            bg.save(out_file, "JPEG", quality=95)
        else:
            bg.save(out_file, "PNG")

        stat = out_file.stat()
        sha = hashlib.sha256(out_file.read_bytes()).hexdigest()

        manifest.assets.append(RenderedAsset(
            path=str(out_file),
            filename=out_file.name,
            format=fmt,
            width=w,
            height=h,
            file_size_bytes=stat.st_size,
            checksum_sha256=sha,
        ))
        return manifest

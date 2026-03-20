"""Shared models for the media rendering pipeline."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# ── Aspect ratio templates ──────────────────────────────────────


class AspectRatio(str, Enum):
    PORTRAIT = "9:16"   # Reels, TikTok, Shorts
    SQUARE = "1:1"      # Instagram feed, podcast art
    LANDSCAPE = "16:9"  # YouTube, Twitter/X


ASPECT_DIMENSIONS: dict[AspectRatio, tuple[int, int]] = {
    AspectRatio.PORTRAIT: (1080, 1920),
    AspectRatio.SQUARE: (1080, 1080),
    AspectRatio.LANDSCAPE: (1920, 1080),
}


# ── Render spec (input) ─────────────────────────────────────────


class RenderType(str, Enum):
    CAPTION_BURN = "caption_burn"
    WAVEFORM = "waveform"
    LYRIC_CARD = "lyric_card"
    HOOK_VARIANT = "hook_variant"
    TEASER = "teaser"


class CaptionStyle(BaseModel):
    font: str = "Arial"
    font_size: int = 48
    font_color: str = "white"
    outline_color: str = "black"
    outline_width: int = 2
    position: str = "bottom"  # top, center, bottom
    margin_bottom: int = 80


class Caption(BaseModel):
    text: str
    start: float  # seconds
    end: float    # seconds


class RenderSpec(BaseModel):
    """Describes a single render job."""

    render_type: RenderType
    aspect_ratio: AspectRatio = AspectRatio.PORTRAIT
    output_format: str = "mp4"  # mp4, png, jpg, wav

    # Source files (not all are needed for every render type)
    video_path: str | None = None
    audio_path: str | None = None
    artwork_path: str | None = None

    # Content
    captions: list[Caption] = Field(default_factory=list)
    caption_style: CaptionStyle = Field(default_factory=CaptionStyle)
    lyrics: str = ""
    hook_timestamps: list[tuple[float, float]] = Field(default_factory=list)
    hook_count: int = 3

    # Text overlays for teasers/lyric cards
    title: str = ""
    subtitle: str = ""
    colors: dict[str, str] = Field(default_factory=lambda: {
        "text": "#FFFFFF",
        "shadow": "#00000080",
        "overlay": "#00000099",
    })

    # Metadata passthrough
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Asset manifest (output) ─────────────────────────────────────


class RenderedAsset(BaseModel):
    """A single file produced by a render."""

    path: str
    filename: str
    format: str          # mp4, png, jpg, wav
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    file_size_bytes: int = 0
    checksum_sha256: str = ""


class AssetManifest(BaseModel):
    """Manifest describing all outputs from a render job."""

    render_type: RenderType
    aspect_ratio: AspectRatio
    created_at: datetime = Field(default_factory=datetime.utcnow)
    source_spec: dict[str, Any] = Field(default_factory=dict)
    assets: list[RenderedAsset] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.assets) > 0 and len(self.errors) == 0

"""Tests for media pipeline models — RenderSpec, AssetManifest, aspect ratios."""

from __future__ import annotations

import json

import pytest

from amplify.media.models import (
    ASPECT_DIMENSIONS,
    AspectRatio,
    AssetManifest,
    Caption,
    CaptionStyle,
    RenderSpec,
    RenderType,
    RenderedAsset,
)


# ── AspectRatio ──────────────────────────────────────────────────


class TestAspectRatio:
    def test_portrait_dimensions(self):
        assert ASPECT_DIMENSIONS[AspectRatio.PORTRAIT] == (1080, 1920)

    def test_square_dimensions(self):
        assert ASPECT_DIMENSIONS[AspectRatio.SQUARE] == (1080, 1080)

    def test_landscape_dimensions(self):
        assert ASPECT_DIMENSIONS[AspectRatio.LANDSCAPE] == (1920, 1080)

    def test_all_ratios_have_dimensions(self):
        for ratio in AspectRatio:
            assert ratio in ASPECT_DIMENSIONS

    def test_enum_values(self):
        assert AspectRatio.PORTRAIT.value == "9:16"
        assert AspectRatio.SQUARE.value == "1:1"
        assert AspectRatio.LANDSCAPE.value == "16:9"


# ── RenderSpec ───────────────────────────────────────────────────


class TestRenderSpec:
    def test_defaults(self):
        spec = RenderSpec(render_type=RenderType.LYRIC_CARD)
        assert spec.aspect_ratio == AspectRatio.PORTRAIT
        assert spec.output_format == "mp4"
        assert spec.captions == []
        assert spec.lyrics == ""
        assert spec.hook_timestamps == []
        assert spec.hook_count == 3

    def test_caption_burn_spec(self):
        spec = RenderSpec(
            render_type=RenderType.CAPTION_BURN,
            aspect_ratio=AspectRatio.PORTRAIT,
            video_path="/tmp/video.mp4",
            captions=[
                Caption(text="Hello world", start=0.0, end=3.0),
                Caption(text="Second line", start=3.0, end=6.0),
            ],
        )
        assert spec.render_type == RenderType.CAPTION_BURN
        assert len(spec.captions) == 2
        assert spec.captions[0].text == "Hello world"

    def test_waveform_spec(self):
        spec = RenderSpec(
            render_type=RenderType.WAVEFORM,
            aspect_ratio=AspectRatio.SQUARE,
            audio_path="/tmp/audio.wav",
            artwork_path="/tmp/art.png",
        )
        assert spec.render_type == RenderType.WAVEFORM
        assert spec.aspect_ratio == AspectRatio.SQUARE

    def test_hook_variant_spec(self):
        spec = RenderSpec(
            render_type=RenderType.HOOK_VARIANT,
            audio_path="/tmp/track.wav",
            hook_timestamps=[(0.0, 15.0), (30.0, 45.0), (60.0, 75.0)],
            hook_count=2,
        )
        assert len(spec.hook_timestamps) == 3
        assert spec.hook_count == 2

    def test_json_roundtrip(self):
        spec = RenderSpec(
            render_type=RenderType.TEASER,
            aspect_ratio=AspectRatio.LANDSCAPE,
            audio_path="/tmp/teaser.wav",
            artwork_path="/tmp/cover.jpg",
            title="Coming Soon",
            subtitle="March 29",
        )
        data = json.loads(spec.model_dump_json())
        restored = RenderSpec(**data)
        assert restored.render_type == spec.render_type
        assert restored.title == "Coming Soon"
        assert restored.subtitle == "March 29"

    def test_caption_style_defaults(self):
        style = CaptionStyle()
        assert style.font == "Arial"
        assert style.font_size == 48
        assert style.font_color == "white"
        assert style.outline_width == 2
        assert style.position == "bottom"

    def test_metadata_passthrough(self):
        spec = RenderSpec(
            render_type=RenderType.LYRIC_CARD,
            metadata={"campaign_id": "abc", "track": "For Love of Country"},
        )
        assert spec.metadata["campaign_id"] == "abc"

    def test_schema_generation(self):
        schema = RenderSpec.model_json_schema()
        assert "render_type" in schema["properties"]
        assert "aspect_ratio" in schema["properties"]


# ── AssetManifest ────────────────────────────────────────────────


class TestAssetManifest:
    def test_empty_manifest_not_success(self):
        m = AssetManifest(render_type=RenderType.LYRIC_CARD, aspect_ratio=AspectRatio.PORTRAIT)
        assert not m.success

    def test_manifest_with_errors_not_success(self):
        m = AssetManifest(
            render_type=RenderType.WAVEFORM,
            aspect_ratio=AspectRatio.SQUARE,
            errors=["ffmpeg not found"],
        )
        assert not m.success

    def test_manifest_with_asset_is_success(self):
        m = AssetManifest(
            render_type=RenderType.CAPTION_BURN,
            aspect_ratio=AspectRatio.PORTRAIT,
            assets=[RenderedAsset(
                path="/tmp/out.mp4",
                filename="out.mp4",
                format="mp4",
                width=1080,
                height=1920,
                file_size_bytes=12345,
                checksum_sha256="abc123",
            )],
        )
        assert m.success

    def test_manifest_with_asset_and_error_not_success(self):
        m = AssetManifest(
            render_type=RenderType.HOOK_VARIANT,
            aspect_ratio=AspectRatio.PORTRAIT,
            assets=[RenderedAsset(path="/tmp/a.wav", filename="a.wav", format="wav")],
            errors=["Hook B failed"],
        )
        assert not m.success

    def test_json_roundtrip(self):
        m = AssetManifest(
            render_type=RenderType.TEASER,
            aspect_ratio=AspectRatio.LANDSCAPE,
            assets=[
                RenderedAsset(
                    path="/tmp/teaser.mp4",
                    filename="teaser.mp4",
                    format="mp4",
                    width=1920,
                    height=1080,
                    duration_seconds=15.0,
                    file_size_bytes=500000,
                    checksum_sha256="deadbeef",
                ),
            ],
        )
        data = json.loads(m.model_dump_json())
        assert data["assets"][0]["checksum_sha256"] == "deadbeef"
        assert data["assets"][0]["duration_seconds"] == 15.0

    def test_rendered_asset_defaults(self):
        a = RenderedAsset(path="/tmp/x.png", filename="x.png", format="png")
        assert a.width is None
        assert a.height is None
        assert a.duration_seconds is None
        assert a.file_size_bytes == 0
        assert a.checksum_sha256 == ""


# ── RenderType enum ──────────────────────────────────────────────


class TestRenderType:
    def test_all_types(self):
        types = {t.value for t in RenderType}
        assert types == {"caption_burn", "waveform", "lyric_card", "hook_variant", "teaser"}

"""Tests for media renderers.

Uses generated sample fixtures (tiny images/audio). Tests that require
ffmpeg are marked and skip gracefully if ffmpeg is not installed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from amplify.media.models import (
    AspectRatio,
    Caption,
    CaptionStyle,
    RenderSpec,
    RenderType,
)
from amplify.media.renderers.caption_burner import (
    CaptionBurner,
    _build_drawtext_filters,
    _escape_drawtext,
    _position_y,
)
from amplify.media.renderers.hook_variants import HookVariantGenerator
from amplify.media.renderers.lyric_card import LyricCardRenderer, _hex_to_rgba, _wrap_text
from amplify.media.renderers.teaser import TeaserRenderer
from amplify.media.renderers.waveform_video import WaveformRenderer


HAS_FFMPEG = shutil.which("ffmpeg") is not None
skip_no_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


# ── Caption Burner helpers ───────────────────────────────────────


class TestCaptionBurnerHelpers:
    def test_escape_drawtext_colon(self):
        assert "\\:" in _escape_drawtext("hello: world")

    def test_escape_drawtext_quote(self):
        assert "'\\'" in _escape_drawtext("it's")

    def test_escape_drawtext_backslash(self):
        assert "\\\\" in _escape_drawtext("back\\slash")

    def test_position_y_top(self):
        style = CaptionStyle(position="top")
        assert _position_y(style, 1920) == "40"

    def test_position_y_center(self):
        style = CaptionStyle(position="center")
        result = _position_y(style, 1920)
        assert "960" in result or "1920/2" in result

    def test_position_y_bottom(self):
        style = CaptionStyle(position="bottom", margin_bottom=100)
        result = _position_y(style, 1920)
        assert "100" in result

    def test_build_drawtext_filters(self):
        captions = [
            Caption(text="Line 1", start=0, end=3),
            Caption(text="Line 2", start=3, end=6),
        ]
        style = CaptionStyle()
        filters = _build_drawtext_filters(captions, style, 1920)
        assert len(filters) == 2
        assert "drawtext=" in filters[0]
        assert "enable=" in filters[0]
        assert "between(t,0" in filters[0]


class TestCaptionBurnerValidation:
    @pytest.mark.asyncio
    async def test_missing_video_path(self, tmp_dir):
        burner = CaptionBurner()
        spec = RenderSpec(render_type=RenderType.CAPTION_BURN)
        manifest = await burner.render(spec, tmp_dir)
        assert not manifest.success
        assert "video_path is required" in manifest.errors[0]

    @pytest.mark.asyncio
    async def test_video_not_found(self, tmp_dir):
        burner = CaptionBurner()
        spec = RenderSpec(
            render_type=RenderType.CAPTION_BURN,
            video_path="/nonexistent/video.mp4",
            captions=[Caption(text="hi", start=0, end=1)],
        )
        manifest = await burner.render(spec, tmp_dir)
        assert not manifest.success
        assert "not found" in manifest.errors[0].lower()

    @pytest.mark.asyncio
    async def test_no_captions(self, tmp_dir, sample_video_mp4):
        burner = CaptionBurner()
        spec = RenderSpec(
            render_type=RenderType.CAPTION_BURN,
            video_path=str(sample_video_mp4),
        )
        manifest = await burner.render(spec, tmp_dir)
        assert not manifest.success
        assert "No captions" in manifest.errors[0]

    @pytest.mark.asyncio
    @skip_no_ffmpeg
    async def test_render_with_ffmpeg(self, tmp_dir, sample_video_mp4):
        burner = CaptionBurner()
        spec = RenderSpec(
            render_type=RenderType.CAPTION_BURN,
            video_path=str(sample_video_mp4),
            captions=[Caption(text="Hello", start=0.0, end=0.5)],
            aspect_ratio=AspectRatio.PORTRAIT,
        )
        manifest = await burner.render(spec, tmp_dir)
        assert manifest.success
        assert len(manifest.assets) == 1
        assert manifest.assets[0].format == "mp4"
        assert manifest.assets[0].width == 1080
        assert manifest.assets[0].height == 1920
        assert Path(manifest.assets[0].path).exists()

    @pytest.mark.asyncio
    async def test_ffmpeg_not_found(self, tmp_dir, sample_video_mp4):
        burner = CaptionBurner(ffmpeg_path="/nonexistent/ffmpeg")
        spec = RenderSpec(
            render_type=RenderType.CAPTION_BURN,
            video_path=str(sample_video_mp4),
            captions=[Caption(text="Hi", start=0, end=1)],
        )
        manifest = await burner.render(spec, tmp_dir)
        assert not manifest.success
        assert "ffmpeg" in manifest.errors[0].lower()


# ── Waveform Renderer ───────────────────────────────────────────


class TestWaveformValidation:
    @pytest.mark.asyncio
    async def test_missing_audio(self, tmp_dir):
        renderer = WaveformRenderer()
        spec = RenderSpec(render_type=RenderType.WAVEFORM, artwork_path="/tmp/x.png")
        manifest = await renderer.render(spec, tmp_dir)
        assert not manifest.success
        assert "audio_path is required" in manifest.errors[0]

    @pytest.mark.asyncio
    async def test_missing_artwork(self, tmp_dir):
        renderer = WaveformRenderer()
        spec = RenderSpec(render_type=RenderType.WAVEFORM, audio_path="/tmp/x.wav")
        manifest = await renderer.render(spec, tmp_dir)
        assert not manifest.success
        assert "artwork_path is required" in manifest.errors[0]

    @pytest.mark.asyncio
    async def test_audio_not_found(self, tmp_dir, sample_artwork):
        renderer = WaveformRenderer()
        spec = RenderSpec(
            render_type=RenderType.WAVEFORM,
            audio_path="/nonexistent/audio.wav",
            artwork_path=str(sample_artwork),
        )
        manifest = await renderer.render(spec, tmp_dir)
        assert not manifest.success

    @pytest.mark.asyncio
    @skip_no_ffmpeg
    async def test_render_waveform(self, tmp_dir, sample_audio_wav, sample_artwork):
        renderer = WaveformRenderer()
        spec = RenderSpec(
            render_type=RenderType.WAVEFORM,
            audio_path=str(sample_audio_wav),
            artwork_path=str(sample_artwork),
            aspect_ratio=AspectRatio.SQUARE,
        )
        manifest = await renderer.render(spec, tmp_dir)
        assert manifest.success
        assert manifest.assets[0].width == 1080
        assert manifest.assets[0].height == 1080


# ── Lyric Card Renderer ─────────────────────────────────────────


class TestLyricCardHelpers:
    def test_hex_to_rgba_6(self):
        assert _hex_to_rgba("#FF0000") == (255, 0, 0, 255)

    def test_hex_to_rgba_8(self):
        assert _hex_to_rgba("#FF000080") == (255, 0, 0, 128)

    def test_hex_to_rgba_invalid(self):
        assert _hex_to_rgba("bad") == (255, 255, 255, 255)


class TestLyricCardRenderer:
    @pytest.mark.asyncio
    async def test_missing_lyrics(self, tmp_dir):
        renderer = LyricCardRenderer()
        spec = RenderSpec(render_type=RenderType.LYRIC_CARD)
        manifest = await renderer.render(spec, tmp_dir)
        assert not manifest.success
        assert "lyrics" in manifest.errors[0].lower()

    @pytest.mark.asyncio
    async def test_render_without_artwork(self, tmp_dir):
        renderer = LyricCardRenderer()
        spec = RenderSpec(
            render_type=RenderType.LYRIC_CARD,
            lyrics="I'll hold you close through the darkest nights",
            title="For Love of Country",
            subtitle="Drew Baird",
            aspect_ratio=AspectRatio.PORTRAIT,
            output_format="png",
        )
        manifest = await renderer.render(spec, tmp_dir)
        assert manifest.success
        assert manifest.assets[0].format == "png"
        assert manifest.assets[0].width == 1080
        assert manifest.assets[0].height == 1920
        assert Path(manifest.assets[0].path).exists()
        assert manifest.assets[0].file_size_bytes > 0
        assert manifest.assets[0].checksum_sha256 != ""

    @pytest.mark.asyncio
    async def test_render_with_artwork(self, tmp_dir, sample_artwork):
        renderer = LyricCardRenderer()
        spec = RenderSpec(
            render_type=RenderType.LYRIC_CARD,
            lyrics="Living for something bigger than me",
            artwork_path=str(sample_artwork),
            aspect_ratio=AspectRatio.SQUARE,
            output_format="png",
        )
        manifest = await renderer.render(spec, tmp_dir)
        assert manifest.success
        assert manifest.assets[0].width == 1080
        assert manifest.assets[0].height == 1080

    @pytest.mark.asyncio
    async def test_render_jpg_output(self, tmp_dir):
        renderer = LyricCardRenderer()
        spec = RenderSpec(
            render_type=RenderType.LYRIC_CARD,
            lyrics="Test lyric",
            aspect_ratio=AspectRatio.LANDSCAPE,
            output_format="jpg",
        )
        manifest = await renderer.render(spec, tmp_dir)
        assert manifest.success
        assert manifest.assets[0].format == "jpg"
        assert manifest.assets[0].width == 1920
        assert manifest.assets[0].height == 1080

    @pytest.mark.asyncio
    async def test_render_all_aspect_ratios(self, tmp_dir):
        renderer = LyricCardRenderer()
        for ratio in AspectRatio:
            spec = RenderSpec(
                render_type=RenderType.LYRIC_CARD,
                lyrics=f"Test for {ratio.value}",
                aspect_ratio=ratio,
                output_format="png",
            )
            manifest = await renderer.render(spec, tmp_dir / ratio.value.replace(":", "_"))
            assert manifest.success, f"Failed for {ratio.value}"


# ── Hook Variant Generator ───────────────────────────────────────


class TestHookVariantValidation:
    @pytest.mark.asyncio
    async def test_missing_audio(self, tmp_dir):
        gen = HookVariantGenerator()
        spec = RenderSpec(
            render_type=RenderType.HOOK_VARIANT,
            hook_timestamps=[(0, 5)],
        )
        manifest = await gen.generate(spec, tmp_dir)
        assert not manifest.success
        assert "audio_path is required" in manifest.errors[0]

    @pytest.mark.asyncio
    async def test_missing_timestamps(self, tmp_dir, sample_audio_wav):
        gen = HookVariantGenerator()
        spec = RenderSpec(
            render_type=RenderType.HOOK_VARIANT,
            audio_path=str(sample_audio_wav),
        )
        manifest = await gen.generate(spec, tmp_dir)
        assert not manifest.success
        assert "hook_timestamps" in manifest.errors[0]

    @pytest.mark.asyncio
    @skip_no_ffmpeg
    async def test_generate_hooks(self, tmp_dir, sample_audio_wav):
        gen = HookVariantGenerator()
        spec = RenderSpec(
            render_type=RenderType.HOOK_VARIANT,
            audio_path=str(sample_audio_wav),
            hook_timestamps=[(0.0, 0.2), (0.1, 0.3)],
            hook_count=2,
        )
        manifest = await gen.generate(spec, tmp_dir)
        assert manifest.success
        assert len(manifest.assets) == 2
        assert manifest.assets[0].filename.startswith("hook_A_")
        assert manifest.assets[1].filename.startswith("hook_B_")

    @pytest.mark.asyncio
    @skip_no_ffmpeg
    async def test_hook_count_limits(self, tmp_dir, sample_audio_wav):
        gen = HookVariantGenerator()
        spec = RenderSpec(
            render_type=RenderType.HOOK_VARIANT,
            audio_path=str(sample_audio_wav),
            hook_timestamps=[(0.0, 0.1), (0.1, 0.2), (0.2, 0.3)],
            hook_count=1,
        )
        manifest = await gen.generate(spec, tmp_dir)
        assert len(manifest.assets) == 1

    @pytest.mark.asyncio
    async def test_invalid_duration(self, tmp_dir, sample_audio_wav):
        gen = HookVariantGenerator()
        spec = RenderSpec(
            render_type=RenderType.HOOK_VARIANT,
            audio_path=str(sample_audio_wav),
            hook_timestamps=[(5.0, 3.0)],  # end before start
        )
        # With mocked ffmpeg to avoid needing it
        with patch("amplify.media.renderers.hook_variants.subprocess.run"):
            manifest = await gen.generate(spec, tmp_dir)
        assert "invalid duration" in manifest.errors[0].lower()


# ── Teaser Renderer ──────────────────────────────────────────────


class TestTeaserValidation:
    @pytest.mark.asyncio
    async def test_missing_audio(self, tmp_dir):
        renderer = TeaserRenderer()
        spec = RenderSpec(render_type=RenderType.TEASER, artwork_path="/tmp/x.png")
        manifest = await renderer.render(spec, tmp_dir)
        assert not manifest.success

    @pytest.mark.asyncio
    async def test_missing_artwork(self, tmp_dir):
        renderer = TeaserRenderer()
        spec = RenderSpec(render_type=RenderType.TEASER, audio_path="/tmp/x.wav")
        manifest = await renderer.render(spec, tmp_dir)
        assert not manifest.success

    @pytest.mark.asyncio
    @skip_no_ffmpeg
    async def test_render_teaser(self, tmp_dir, sample_audio_wav, sample_artwork):
        renderer = TeaserRenderer()
        spec = RenderSpec(
            render_type=RenderType.TEASER,
            audio_path=str(sample_audio_wav),
            artwork_path=str(sample_artwork),
            title="For Love of Country",
            subtitle="Out March 29",
            aspect_ratio=AspectRatio.PORTRAIT,
        )
        manifest = await renderer.render(spec, tmp_dir)
        assert manifest.success
        assert manifest.assets[0].width == 1080
        assert manifest.assets[0].height == 1920

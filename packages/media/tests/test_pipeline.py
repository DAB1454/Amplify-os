"""Tests for the render pipeline dispatcher."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from amplify.media.cache import RenderCache
from amplify.media.models import (
    AspectRatio,
    AssetManifest,
    Caption,
    RenderedAsset,
    RenderSpec,
    RenderType,
)
from amplify.media.render_pipeline import RenderPipeline


@pytest.fixture
def cache(tmp_path: Path) -> RenderCache:
    return RenderCache(cache_dir=tmp_path / "pipeline_cache")


@pytest.fixture
def pipeline(cache: RenderCache) -> RenderPipeline:
    return RenderPipeline(cache=cache)


def _ok_manifest(render_type: RenderType) -> AssetManifest:
    return AssetManifest(
        render_type=render_type,
        aspect_ratio=AspectRatio.PORTRAIT,
        assets=[RenderedAsset(path="/tmp/out.mp4", filename="out.mp4", format="mp4")],
    )


class TestRenderPipeline:
    @pytest.mark.asyncio
    async def test_dispatches_lyric_card(self, pipeline):
        spec = RenderSpec(
            render_type=RenderType.LYRIC_CARD,
            lyrics="Test lyrics",
            output_format="png",
        )
        with patch.object(pipeline._lyric, "render", new_callable=AsyncMock) as mock:
            mock.return_value = _ok_manifest(RenderType.LYRIC_CARD)
            result = await pipeline.render(spec)
            mock.assert_called_once()
        assert result.success

    @pytest.mark.asyncio
    async def test_dispatches_caption_burn(self, pipeline):
        spec = RenderSpec(
            render_type=RenderType.CAPTION_BURN,
            video_path="/tmp/v.mp4",
            captions=[Caption(text="Hi", start=0, end=1)],
        )
        with patch.object(pipeline._caption, "render", new_callable=AsyncMock) as mock:
            mock.return_value = _ok_manifest(RenderType.CAPTION_BURN)
            result = await pipeline.render(spec)
            mock.assert_called_once()
        assert result.success

    @pytest.mark.asyncio
    async def test_dispatches_waveform(self, pipeline):
        spec = RenderSpec(
            render_type=RenderType.WAVEFORM,
            audio_path="/tmp/a.wav",
            artwork_path="/tmp/art.png",
        )
        with patch.object(pipeline._waveform, "render", new_callable=AsyncMock) as mock:
            mock.return_value = _ok_manifest(RenderType.WAVEFORM)
            result = await pipeline.render(spec)
            mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatches_hook_variant(self, pipeline):
        spec = RenderSpec(
            render_type=RenderType.HOOK_VARIANT,
            audio_path="/tmp/a.wav",
            hook_timestamps=[(0, 5)],
        )
        with patch.object(pipeline._hook, "generate", new_callable=AsyncMock) as mock:
            mock.return_value = _ok_manifest(RenderType.HOOK_VARIANT)
            result = await pipeline.render(spec)
            mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatches_teaser(self, pipeline):
        spec = RenderSpec(
            render_type=RenderType.TEASER,
            audio_path="/tmp/a.wav",
            artwork_path="/tmp/art.png",
        )
        with patch.object(pipeline._teaser, "render", new_callable=AsyncMock) as mock:
            mock.return_value = _ok_manifest(RenderType.TEASER)
            result = await pipeline.render(spec)
            mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_hit(self, pipeline, cache):
        spec = RenderSpec(
            render_type=RenderType.LYRIC_CARD,
            lyrics="Cached lyrics",
            output_format="png",
        )
        # Pre-populate cache
        d = cache.cache_dir_for(spec.model_dump())
        (d / "cached.png").write_bytes(b"fake png data")

        # Should NOT call the renderer
        with patch.object(pipeline._lyric, "render", new_callable=AsyncMock) as mock:
            result = await pipeline.render(spec)
            mock.assert_not_called()

        assert len(result.assets) == 1
        assert result.assets[0].filename == "cached.png"

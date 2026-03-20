"""Tests for the render_media worker job — retry logic and dispatch."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.jobs.render_media import MAX_RETRIES, render_media


@pytest.fixture
def valid_lyric_payload():
    return {
        "render_type": "lyric_card",
        "aspect_ratio": "9:16",
        "lyrics": "I'll hold you close through the darkest nights",
        "title": "For Love of Country",
        "subtitle": "Drew Baird",
        "output_format": "png",
    }


@pytest.fixture
def invalid_payload():
    return {"render_type": "not_a_real_type"}


class TestRenderMediaJob:
    @pytest.mark.asyncio
    async def test_invalid_spec_returns_error(self, invalid_payload):
        result = await render_media(invalid_payload)
        assert result["status"] == "error"
        assert "Invalid render spec" in result["error"]
        assert result["assets_rendered"] == 0

    @pytest.mark.asyncio
    async def test_successful_render(self, valid_lyric_payload):
        from amplify.media.models import AssetManifest, AspectRatio, RenderedAsset, RenderType

        mock_manifest = AssetManifest(
            render_type=RenderType.LYRIC_CARD,
            aspect_ratio=AspectRatio.PORTRAIT,
            assets=[RenderedAsset(
                path="/tmp/out.png",
                filename="out.png",
                format="png",
                width=1080,
                height=1920,
                file_size_bytes=5000,
                checksum_sha256="abc123",
            )],
        )

        with patch("amplify.media.render_pipeline.RenderPipeline") as MockPipeline:
            instance = MockPipeline.return_value
            instance.render = AsyncMock(return_value=mock_manifest)

            result = await render_media(valid_lyric_payload)

        assert result["status"] == "ok"
        assert result["assets_rendered"] == 1
        assert "manifest" in result

    @pytest.mark.asyncio
    async def test_non_retryable_error_returns_immediately(self, valid_lyric_payload):
        from amplify.media.models import AssetManifest, AspectRatio, RenderType

        error_manifest = AssetManifest(
            render_type=RenderType.LYRIC_CARD,
            aspect_ratio=AspectRatio.PORTRAIT,
            errors=["Audio not found: /nonexistent.wav"],
        )

        with patch("amplify.media.render_pipeline.RenderPipeline") as MockPipeline:
            instance = MockPipeline.return_value
            instance.render = AsyncMock(return_value=error_manifest)

            result = await render_media(valid_lyric_payload)
            # Should only call render once (no retry for "not found")
            assert instance.render.call_count == 1

        assert result["status"] == "error"
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_retries_on_transient_failure(self, valid_lyric_payload):
        from amplify.media.models import AssetManifest, AspectRatio, RenderedAsset, RenderType

        fail_manifest = AssetManifest(
            render_type=RenderType.LYRIC_CARD,
            aspect_ratio=AspectRatio.PORTRAIT,
            errors=["ffmpeg crashed with signal 11"],
        )
        ok_manifest = AssetManifest(
            render_type=RenderType.LYRIC_CARD,
            aspect_ratio=AspectRatio.PORTRAIT,
            assets=[RenderedAsset(path="/tmp/out.png", filename="out.png", format="png")],
        )

        with patch("amplify.media.render_pipeline.RenderPipeline") as MockPipeline:
            instance = MockPipeline.return_value
            instance.render = AsyncMock(side_effect=[fail_manifest, ok_manifest])

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await render_media(valid_lyric_payload)

        assert result["status"] == "ok"
        assert instance.render.call_count == 2

    @pytest.mark.asyncio
    async def test_exhausts_retries(self, valid_lyric_payload):
        with patch("amplify.media.render_pipeline.RenderPipeline") as MockPipeline:
            instance = MockPipeline.return_value
            instance.render = AsyncMock(side_effect=RuntimeError("boom"))

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await render_media(valid_lyric_payload)

        assert result["status"] == "error"
        assert f"Failed after {MAX_RETRIES}" in result["error"]
        assert instance.render.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_empty_payload(self):
        result = await render_media({})
        assert result["status"] == "error"

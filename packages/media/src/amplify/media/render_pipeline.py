"""Central render dispatcher — routes RenderSpec to the correct renderer."""

from __future__ import annotations

import logging
from pathlib import Path

from amplify.media.cache import RenderCache
from amplify.media.models import AssetManifest, RenderedAsset, RenderSpec, RenderType
from amplify.media.renderers.caption_burner import CaptionBurner
from amplify.media.renderers.hook_variants import HookVariantGenerator
from amplify.media.renderers.lyric_card import LyricCardRenderer
from amplify.media.renderers.teaser import TeaserRenderer
from amplify.media.renderers.waveform_video import WaveformRenderer

logger = logging.getLogger(__name__)


class RenderPipeline:
    """Dispatches render specs to the appropriate renderer.

    Integrates with RenderCache for deduplication.
    """

    def __init__(
        self,
        cache: RenderCache | None = None,
        ffmpeg_path: str = "ffmpeg",
    ) -> None:
        self.cache = cache or RenderCache()
        self._caption = CaptionBurner(ffmpeg_path)
        self._waveform = WaveformRenderer(ffmpeg_path)
        self._lyric = LyricCardRenderer()
        self._hook = HookVariantGenerator(ffmpeg_path)
        self._teaser = TeaserRenderer(ffmpeg_path)

    async def render(self, spec: RenderSpec) -> AssetManifest:
        """Run a render job, returning from cache if available."""
        spec_dict = spec.model_dump()

        # Check cache
        if self.cache.has(spec_dict):
            cached_files = self.cache.get_files(spec_dict)
            logger.info("Cache hit for %s (%d files)", spec.render_type, len(cached_files))
            manifest = AssetManifest(
                render_type=spec.render_type,
                aspect_ratio=spec.aspect_ratio,
            )
            for f in cached_files:
                stat = f.stat()
                manifest.assets.append(RenderedAsset(
                    path=str(f),
                    filename=f.name,
                    format=f.suffix.lstrip("."),
                    file_size_bytes=stat.st_size,
                ))
            return manifest

        # Route to renderer
        output_dir = self.cache.cache_dir_for(spec_dict)

        if spec.render_type == RenderType.CAPTION_BURN:
            return await self._caption.render(spec, output_dir)
        elif spec.render_type == RenderType.WAVEFORM:
            return await self._waveform.render(spec, output_dir)
        elif spec.render_type == RenderType.LYRIC_CARD:
            return await self._lyric.render(spec, output_dir)
        elif spec.render_type == RenderType.HOOK_VARIANT:
            return await self._hook.generate(spec, output_dir)
        elif spec.render_type == RenderType.TEASER:
            return await self._teaser.render(spec, output_dir)
        else:
            manifest = AssetManifest(
                render_type=spec.render_type,
                aspect_ratio=spec.aspect_ratio,
            )
            manifest.errors.append(f"Unknown render type: {spec.render_type}")
            return manifest

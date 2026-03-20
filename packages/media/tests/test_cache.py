"""Tests for the render cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from amplify.media.cache import RenderCache


@pytest.fixture
def cache(tmp_path: Path) -> RenderCache:
    return RenderCache(cache_dir=tmp_path / "cache")


class TestRenderCache:
    def test_empty_cache_has_returns_false(self, cache):
        assert not cache.has({"render_type": "lyric_card"})

    def test_cache_dir_for_creates_directory(self, cache):
        d = cache.cache_dir_for({"render_type": "lyric_card"})
        assert d.exists()
        assert d.is_dir()

    def test_same_spec_same_dir(self, cache):
        spec = {"render_type": "waveform", "audio": "track.wav"}
        d1 = cache.cache_dir_for(spec)
        d2 = cache.cache_dir_for(spec)
        assert d1 == d2

    def test_different_spec_different_dir(self, cache):
        d1 = cache.cache_dir_for({"render_type": "waveform"})
        d2 = cache.cache_dir_for({"render_type": "lyric_card"})
        assert d1 != d2

    def test_has_after_writing_file(self, cache):
        spec = {"render_type": "test"}
        d = cache.cache_dir_for(spec)
        (d / "output.mp4").write_bytes(b"fake video data")
        assert cache.has(spec)

    def test_get_files(self, cache):
        spec = {"render_type": "test"}
        d = cache.cache_dir_for(spec)
        (d / "a.mp4").write_bytes(b"aaa")
        (d / "b.mp4").write_bytes(b"bbb")
        files = cache.get_files(spec)
        assert len(files) == 2

    def test_get_files_empty(self, cache):
        assert cache.get_files({"render_type": "nonexistent"}) == []

    def test_evict(self, cache):
        spec = {"render_type": "evict_test"}
        d = cache.cache_dir_for(spec)
        (d / "out.mp4").write_bytes(b"data")
        assert cache.has(spec)
        cache.evict(spec)
        assert not cache.has(spec)

    def test_evict_nonexistent(self, cache):
        # Should not raise
        cache.evict({"render_type": "doesnt_exist"})

    def test_clear(self, cache):
        spec = {"render_type": "clear_test"}
        d = cache.cache_dir_for(spec)
        (d / "out.mp4").write_bytes(b"data")
        cache.clear()
        assert not cache.has(spec)
        # Cache dir itself should still exist (recreated)
        assert cache._dir.exists()

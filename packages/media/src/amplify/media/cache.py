"""Render cache for the media pipeline.

Wraps content-addressed storage so identical render specs hit cache
instead of re-running ffmpeg.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path.home() / ".amplify-os" / "media-cache"


class RenderCache:
    """Content-addressed file cache keyed by render spec hash."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._dir = cache_dir or DEFAULT_CACHE_DIR
        self._dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _hash_key(spec_dict: dict) -> str:
        raw = json.dumps(spec_dict, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def cache_dir_for(self, spec_dict: dict) -> Path:
        """Return a dedicated directory for a given spec hash."""
        h = self._hash_key(spec_dict)
        d = self._dir / h
        d.mkdir(parents=True, exist_ok=True)
        return d

    def has(self, spec_dict: dict) -> bool:
        d = self._dir / self._hash_key(spec_dict)
        if not d.exists():
            return False
        return any(d.iterdir())

    def get_files(self, spec_dict: dict) -> list[Path]:
        d = self._dir / self._hash_key(spec_dict)
        if not d.exists():
            return []
        return sorted(d.iterdir())

    def evict(self, spec_dict: dict) -> None:
        import shutil
        d = self._dir / self._hash_key(spec_dict)
        if d.exists():
            shutil.rmtree(d)
            logger.info("Evicted cache entry %s", d)

    def clear(self) -> None:
        import shutil
        if self._dir.exists():
            shutil.rmtree(self._dir)
            self._dir.mkdir(parents=True, exist_ok=True)
            logger.info("Cleared render cache at %s", self._dir)

"""Local filesystem cache for rendered assets."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path.home() / ".amplify-os" / "cache"


class FilesystemCache:
    """Cache rendered assets on the local filesystem."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _key_path(self, key: str) -> Path:
        hashed = hashlib.sha256(key.encode()).hexdigest()
        return self._cache_dir / hashed

    def get(self, key: str) -> Path | None:
        """Return the cached file path, or None if not present."""
        path = self._key_path(key)
        if path.exists():
            return path
        return None

    def put(self, key: str, data: bytes) -> Path:
        """Write data to cache and return the file path."""
        path = self._key_path(key)
        path.write_bytes(data)
        logger.info("Cached %d bytes at %s", len(data), path)
        return path

    def evict(self, key: str) -> None:
        """Remove an entry from the cache."""
        path = self._key_path(key)
        if path.exists():
            path.unlink()
            logger.info("Evicted cache entry %s", path)

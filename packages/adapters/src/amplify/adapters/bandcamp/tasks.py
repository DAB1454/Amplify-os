"""Bandcamp catalog management tasks."""

from __future__ import annotations


class BandcampTasks:
    """Update release metadata and pricing on Bandcamp."""

    def __init__(self, session: dict | None = None) -> None:
        self.session = session

    async def update_description(self, release_id: str, text: str) -> bool:
        """Update the description of a release. Returns True on success."""
        raise NotImplementedError("TODO")

    async def set_pricing(self, release_id: str, prices: dict) -> bool:
        """Set pricing for a release (e.g. {'digital': 7.0, 'name_your_price': True}). Returns True on success."""
        raise NotImplementedError("TODO")

"""Bandcamp catalog and sales data."""

from __future__ import annotations


class BandcampCatalog:
    """Access Bandcamp releases and sales data."""

    def __init__(self, session: dict | None = None) -> None:
        self.session = session

    async def get_releases(self) -> list:
        """Fetch all releases from the authenticated Bandcamp account."""
        raise NotImplementedError("TODO")

    async def get_sales(self, period: str) -> dict:
        """Fetch sales data for the given period."""
        raise NotImplementedError("TODO")

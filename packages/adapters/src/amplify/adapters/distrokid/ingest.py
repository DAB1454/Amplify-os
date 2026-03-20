"""Ingest release and earnings data from DistroKid."""

from __future__ import annotations

from .models import DistroKidEarnings, DistroKidRelease


class DistroKidIngestor:
    """Ingest release and earnings data from DistroKid."""

    def __init__(self) -> None:
        pass

    async def get_releases(self, credentials: dict) -> list[DistroKidRelease]:
        """Fetch all releases from DistroKid for the authenticated user."""
        raise NotImplementedError("TODO")

    async def get_earnings(self, credentials: dict, period: str) -> DistroKidEarnings:
        """Fetch earnings data for the given period."""
        raise NotImplementedError("TODO")

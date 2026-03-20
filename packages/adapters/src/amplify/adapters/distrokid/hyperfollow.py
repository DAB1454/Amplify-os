"""Manage DistroKid HyperFollow smart links."""

from __future__ import annotations


class HyperfollowManager:
    """Create and track HyperFollow pre-save / smart links."""

    def __init__(self) -> None:
        pass

    async def create_link(self, release_id: str, platforms: list[str]) -> str:
        """Create a HyperFollow link for the given release and platforms.

        Returns the HyperFollow URL.
        """
        raise NotImplementedError("TODO")

    async def get_stats(self, link_id: str) -> dict:
        """Retrieve click and save stats for a HyperFollow link."""
        raise NotImplementedError("TODO")

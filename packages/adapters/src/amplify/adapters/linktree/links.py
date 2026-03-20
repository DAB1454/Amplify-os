"""Linktree link management."""

from __future__ import annotations


class LinktreeLinks:
    """Manage links on a Linktree profile."""

    def __init__(self, access_token: str = "") -> None:
        self.access_token = access_token

    async def list_links(self) -> list:
        """List all links on the profile."""
        raise NotImplementedError("TODO")

    async def add_link(self, title: str, url: str, position: int = 0) -> str:
        """Add a new link. Returns the link ID."""
        raise NotImplementedError("TODO")

    async def remove_link(self, link_id: str) -> bool:
        """Remove a link. Returns True on success."""
        raise NotImplementedError("TODO")

    async def reorder(self, link_ids: list[str]) -> bool:
        """Reorder links by providing the desired order of link IDs. Returns True on success."""
        raise NotImplementedError("TODO")

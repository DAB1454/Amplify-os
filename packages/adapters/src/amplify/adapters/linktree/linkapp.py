"""Smart music link page generator."""

from __future__ import annotations


async def create_music_link(release: dict, platforms: list[str]) -> str:
    """Generate a smart link page that routes fans to the correct streaming platform.

    Args:
        release: Release metadata (title, artist, artwork_url, etc.).
        platforms: List of platform names to include (e.g. ['spotify', 'apple_music', 'youtube']).

    Returns:
        The URL of the generated smart link page.
    """
    raise NotImplementedError("TODO")

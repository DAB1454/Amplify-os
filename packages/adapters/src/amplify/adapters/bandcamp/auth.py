"""Bandcamp authentication."""

from __future__ import annotations


class BandcampAuth:
    """Authenticate with Bandcamp (session-based, no public OAuth API)."""

    def __init__(self) -> None:
        self.session: dict | None = None

    def login(self, email: str, password: str) -> dict:
        """Log in to Bandcamp and return a session dict with cookies/tokens."""
        raise NotImplementedError("TODO")

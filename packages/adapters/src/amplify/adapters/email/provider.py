"""Email sending via SMTP."""

from __future__ import annotations


class EmailProvider:
    """Send transactional and bulk emails via SMTP."""

    def __init__(self, host: str = "", port: int = 587, username: str = "", password: str = "") -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password

    async def send(self, to: str, subject: str, body_html: str) -> bool:
        """Send a single email. Returns True on success."""
        raise NotImplementedError("TODO")

    async def send_bulk(self, recipients: list[str], subject: str, body_html: str) -> dict:
        """Send the same email to multiple recipients.

        Returns a dict with 'success' and 'fail' counts.
        """
        raise NotImplementedError("TODO")

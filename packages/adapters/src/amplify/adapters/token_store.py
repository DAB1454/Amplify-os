"""Secure token storage and refresh handling.

Abstracts token persistence so adapters never touch the DB directly.
Tokens are read from ChannelConnectionModel and refreshed via platform-
specific OAuth flows.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Buffer before actual expiry to trigger proactive refresh
REFRESH_BUFFER = timedelta(minutes=5)


class TokenSet(BaseModel):
    """A set of OAuth tokens with metadata."""

    access_token: str = ""
    refresh_token: str = ""
    expires_at: datetime | None = None
    scopes: list[str] = []
    platform: str = ""
    account_id: str = ""
    extra: dict[str, Any] = {}

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.utcnow() >= self.expires_at

    @property
    def needs_refresh(self) -> bool:
        """True if token will expire within the buffer window."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() >= (self.expires_at - REFRESH_BUFFER)


class TokenStore:
    """In-memory token store that can be backed by a DB persistence layer.

    Usage:
        store = TokenStore()
        store.set("instagram", channel_id, TokenSet(...))
        tokens = store.get("instagram", channel_id)

    For production, subclass and override _persist / _load to write
    to ChannelConnectionModel via SQLAlchemy.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, TokenSet] = {}

    @staticmethod
    def _key(platform: str, channel_id: str) -> str:
        return f"{platform}:{channel_id}"

    def get(self, platform: str, channel_id: str) -> TokenSet | None:
        """Retrieve tokens for a platform+channel. Returns None if not stored."""
        return self._tokens.get(self._key(platform, channel_id))

    def set(self, platform: str, channel_id: str, tokens: TokenSet) -> None:
        """Store tokens for a platform+channel."""
        self._tokens[self._key(platform, channel_id)] = tokens
        logger.debug("Stored tokens for %s:%s", platform, channel_id)
        self._persist(platform, channel_id, tokens)

    def remove(self, platform: str, channel_id: str) -> None:
        """Remove tokens for a platform+channel."""
        self._tokens.pop(self._key(platform, channel_id), None)

    def _persist(self, platform: str, channel_id: str, tokens: TokenSet) -> None:
        """Override in subclass to write tokens to DB."""

    def _load(self, platform: str, channel_id: str) -> TokenSet | None:
        """Override in subclass to load tokens from DB."""
        return None

    @staticmethod
    def from_channel_connection(row: Any, encryptor: Any | None = None) -> TokenSet:
        """Build a TokenSet from a ChannelConnectionModel row.

        If encryptor is provided and the row has token_encrypted=True,
        tokens will be decrypted before building the TokenSet.
        """
        access = row.access_token or ""
        refresh = row.refresh_token or ""

        if encryptor and getattr(row, "token_encrypted", False):
            if access:
                access = encryptor.decrypt(access)
            if refresh:
                refresh = encryptor.decrypt(refresh)

        return TokenSet(
            access_token=access,
            refresh_token=refresh,
            expires_at=row.token_expires_at,
            platform=row.platform,
            account_id=row.platform_account_id or "",
            scopes=getattr(row, "granted_scopes", None) or [],
            extra=row.settings or {},
        )


class DatabaseTokenStore(TokenStore):
    """Token store backed by ChannelConnectionModel rows with encryption."""

    def __init__(self, session: AsyncSession, encryptor: Any) -> None:
        super().__init__()
        self._session = session
        self._encryptor = encryptor

    async def persist_tokens(
        self,
        channel_id: uuid.UUID,
        tokens: TokenSet,
    ) -> None:
        """Encrypt and persist tokens to the channel row.

        Never overwrites a valid refresh token with null/empty.
        """
        from amplify.db.models.channel import ChannelConnectionModel

        result = await self._session.execute(
            select(ChannelConnectionModel).where(ChannelConnectionModel.id == channel_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            logger.error("Channel %s not found for token persist", channel_id)
            return

        encrypted_access = self._encryptor.encrypt(tokens.access_token) if tokens.access_token else ""
        row.access_token = encrypted_access

        # Never overwrite a valid refresh token with null/empty
        if tokens.refresh_token:
            row.refresh_token = self._encryptor.encrypt(tokens.refresh_token)

        row.token_expires_at = tokens.expires_at
        row.token_encrypted = not self._encryptor.is_passthrough
        if tokens.scopes:
            row.granted_scopes = tokens.scopes

        await self._session.flush()
        logger.debug("Persisted encrypted tokens for channel %s", channel_id)

    async def load_tokens(
        self,
        channel_id: uuid.UUID,
    ) -> TokenSet | None:
        """Load and decrypt tokens from a channel row."""
        from amplify.db.models.channel import ChannelConnectionModel

        result = await self._session.execute(
            select(ChannelConnectionModel).where(ChannelConnectionModel.id == channel_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None

        return self.from_channel_connection(row, self._encryptor)

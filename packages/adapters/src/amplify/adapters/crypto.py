"""Token encryption using Fernet symmetric encryption.

Supports key rotation: encrypt always uses the primary key,
decrypt tries primary first then falls back to old keys.
"""

from __future__ import annotations

import logging
import warnings

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class TokenEncryptor:
    """Encrypt / decrypt OAuth tokens with Fernet. Supports key rotation."""

    def __init__(
        self,
        primary_key: str = "",
        old_keys: list[str] | None = None,
        deployment_mode: str = "local",
    ) -> None:
        self._deployment_mode = deployment_mode
        self._passthrough = False

        if not primary_key:
            if deployment_mode == "local":
                warnings.warn(
                    "TOKEN ENCRYPTION DISABLED — running in passthrough mode. "
                    "This is acceptable ONLY for local development.",
                    stacklevel=2,
                )
                logger.warning(
                    "Token encryption is DISABLED (passthrough mode). "
                    "Set TOKEN_ENCRYPTION_KEY for production."
                )
                self._passthrough = True
                self._primary: Fernet | None = None
                self._old: list[Fernet] = []
                return
            else:
                raise RuntimeError(
                    f"TOKEN_ENCRYPTION_KEY is required in deployment_mode={deployment_mode!r}. "
                    "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )

        self._primary = Fernet(primary_key.encode() if isinstance(primary_key, str) else primary_key)
        self._old = []
        for k in (old_keys or []):
            try:
                self._old.append(Fernet(k.encode() if isinstance(k, str) else k))
            except Exception:
                logger.warning("Skipping invalid old encryption key")

    @property
    def is_passthrough(self) -> bool:
        return self._passthrough

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a plaintext string. Returns base64-encoded ciphertext."""
        if self._passthrough:
            return plaintext
        assert self._primary is not None
        return self._primary.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a ciphertext string. Tries primary key first, then old keys."""
        if self._passthrough:
            return ciphertext
        assert self._primary is not None

        # Try primary key first
        try:
            return self._primary.decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            pass

        # Try old keys for rotation
        for old_fernet in self._old:
            try:
                return old_fernet.decrypt(ciphertext.encode()).decode()
            except InvalidToken:
                continue

        raise InvalidToken("Cannot decrypt token with any available key")

"""Encrypted local secrets store using Fernet symmetric encryption."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path.home() / ".amplify-os" / "secrets.enc"
KEY_PATH = Path.home() / ".amplify-os" / ".secrets.key"


def _derive_key(passphrase: str) -> bytes:
    """Derive a Fernet key from a passphrase using SHA-256."""
    digest = hashlib.sha256(passphrase.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _load_or_create_key(key_path: Path) -> bytes:
    """Load an existing key file or generate a new random key."""
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        return key_path.read_bytes().strip()
    key = Fernet.generate_key()
    key_path.write_bytes(key)
    # Restrict file permissions on unix
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass  # Windows doesn't support chmod the same way
    logger.info("Generated new encryption key at %s", key_path)
    return key


class SecretsStore:
    """Encrypted local secret storage backed by Fernet.

    Secrets are encrypted at rest in a single JSON blob. The encryption key
    is either derived from a passphrase or auto-generated and stored in a
    separate key file with restricted permissions.
    """

    def __init__(
        self,
        path: Path | None = None,
        key_path: Path | None = None,
        passphrase: str | None = None,
    ) -> None:
        self._path = path or DEFAULT_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)

        if passphrase:
            key = _derive_key(passphrase)
        else:
            key = _load_or_create_key(key_path or KEY_PATH)

        self._fernet = Fernet(key)

        if not self._path.exists():
            self._save({})

    def _load(self) -> dict[str, str]:
        """Decrypt and parse the secrets file."""
        raw = self._path.read_bytes()
        if not raw:
            return {}
        try:
            decrypted = self._fernet.decrypt(raw)
            return json.loads(decrypted)
        except InvalidToken:
            logger.error("Failed to decrypt secrets — wrong key or corrupted file")
            raise ValueError(
                "Cannot decrypt secrets store. Check your passphrase or key file."
            )

    def _save(self, data: dict[str, str]) -> None:
        """Encrypt and write the secrets file."""
        plaintext = json.dumps(data, indent=2).encode()
        encrypted = self._fernet.encrypt(plaintext)
        self._path.write_bytes(encrypted)

    def get(self, key: str) -> str | None:
        """Retrieve a secret by key."""
        return self._load().get(key)

    def set(self, key: str, value: str) -> None:
        """Store a secret (encrypts immediately)."""
        data = self._load()
        data[key] = value
        self._save(data)
        logger.debug("Secret '%s' stored", key)

    def delete(self, key: str) -> None:
        """Remove a secret by key."""
        data = self._load()
        if data.pop(key, None) is not None:
            self._save(data)
            logger.debug("Secret '%s' deleted", key)

    def list_keys(self) -> list[str]:
        """Return all stored secret names (not values)."""
        return list(self._load().keys())

    def has(self, key: str) -> bool:
        """Check if a secret exists."""
        return key in self._load()

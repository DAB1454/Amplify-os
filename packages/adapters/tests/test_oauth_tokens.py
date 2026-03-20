"""Tests for token encryption, key rotation, and DatabaseTokenStore."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from amplify.adapters.crypto import TokenEncryptor
from amplify.adapters.token_store import TokenSet, TokenStore


# ── TokenEncryptor tests ─────────────────────────────────────────


class TestTokenEncryptor:
    def test_encrypt_decrypt_roundtrip(self):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        enc = TokenEncryptor(primary_key=key, deployment_mode="local")

        plaintext = "my-super-secret-token-12345"
        ciphertext = enc.encrypt(plaintext)
        assert ciphertext != plaintext
        assert enc.decrypt(ciphertext) == plaintext

    def test_passthrough_when_local_no_key(self):
        with pytest.warns(match="TOKEN ENCRYPTION DISABLED"):
            enc = TokenEncryptor(primary_key="", deployment_mode="local")

        assert enc.is_passthrough is True
        assert enc.encrypt("hello") == "hello"
        assert enc.decrypt("hello") == "hello"

    def test_different_keys_fail(self):
        from cryptography.fernet import Fernet, InvalidToken
        key1 = Fernet.generate_key().decode()
        key2 = Fernet.generate_key().decode()

        enc1 = TokenEncryptor(primary_key=key1, deployment_mode="local")
        enc2 = TokenEncryptor(primary_key=key2, deployment_mode="local")

        ciphertext = enc1.encrypt("secret")
        with pytest.raises(InvalidToken):
            enc2.decrypt(ciphertext)

    def test_key_rotation_decrypt_with_old_key(self):
        from cryptography.fernet import Fernet
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()

        old_enc = TokenEncryptor(primary_key=old_key, deployment_mode="local")
        ciphertext = old_enc.encrypt("rotated-secret")

        # New encryptor with old key in rotation list
        new_enc = TokenEncryptor(
            primary_key=new_key,
            old_keys=[old_key],
            deployment_mode="local",
        )
        assert new_enc.decrypt(ciphertext) == "rotated-secret"

    def test_key_rotation_reencrypt_with_primary(self):
        from cryptography.fernet import Fernet
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()

        new_enc = TokenEncryptor(
            primary_key=new_key,
            old_keys=[old_key],
            deployment_mode="local",
        )

        # Encrypt with new primary
        ciphertext = new_enc.encrypt("fresh-secret")

        # Should decrypt with just the new key (no old keys needed)
        primary_only = TokenEncryptor(primary_key=new_key, deployment_mode="local")
        assert primary_only.decrypt(ciphertext) == "fresh-secret"

    def test_startup_fail_nonlocal_no_key(self):
        with pytest.raises(RuntimeError, match="TOKEN_ENCRYPTION_KEY is required"):
            TokenEncryptor(primary_key="", deployment_mode="staging")

        with pytest.raises(RuntimeError, match="TOKEN_ENCRYPTION_KEY is required"):
            TokenEncryptor(primary_key="", deployment_mode="production")


# ── TokenSet tests ───────────────────────────────────────────────


class TestTokenSet:
    def test_token_set_is_expired(self):
        ts = TokenSet(
            access_token="tok",
            expires_at=datetime.utcnow() - timedelta(minutes=1),
        )
        assert ts.is_expired is True

    def test_token_set_not_expired(self):
        ts = TokenSet(
            access_token="tok",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        assert ts.is_expired is False

    def test_token_set_needs_refresh(self):
        ts = TokenSet(
            access_token="tok",
            expires_at=datetime.utcnow() + timedelta(minutes=3),
        )
        assert ts.needs_refresh is True

    def test_token_set_no_expiry(self):
        ts = TokenSet(access_token="tok")
        assert ts.is_expired is False
        assert ts.needs_refresh is False


# ── TokenStore.from_channel_connection tests ─────────────────────


class TestFromChannelConnection:
    def test_from_channel_connection_no_encryption(self):
        row = MagicMock()
        row.access_token = "plain-access"
        row.refresh_token = "plain-refresh"
        row.token_expires_at = datetime(2025, 1, 1)
        row.platform = "instagram"
        row.platform_account_id = "12345"
        row.settings = {"key": "val"}
        row.token_encrypted = False
        row.granted_scopes = ["instagram_basic"]

        ts = TokenStore.from_channel_connection(row)
        assert ts.access_token == "plain-access"
        assert ts.refresh_token == "plain-refresh"

    def test_from_channel_connection_with_encryption(self):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        enc = TokenEncryptor(primary_key=key, deployment_mode="local")

        row = MagicMock()
        row.access_token = enc.encrypt("secret-access")
        row.refresh_token = enc.encrypt("secret-refresh")
        row.token_expires_at = None
        row.platform = "youtube"
        row.platform_account_id = "yt123"
        row.settings = {}
        row.token_encrypted = True
        row.granted_scopes = []

        ts = TokenStore.from_channel_connection(row, encryptor=enc)
        assert ts.access_token == "secret-access"
        assert ts.refresh_token == "secret-refresh"


# ── DatabaseTokenStore tests ─────────────────────────────────────


class TestDatabaseTokenStoreNeverOverwritesRefresh:
    @pytest.mark.asyncio
    async def test_never_overwrites_refresh_with_null(self):
        """If new tokens have empty refresh_token, the existing one is preserved."""
        from cryptography.fernet import Fernet
        from amplify.adapters.token_store import DatabaseTokenStore

        key = Fernet.generate_key().decode()
        enc = TokenEncryptor(primary_key=key, deployment_mode="local")

        # Mock DB session and channel row
        existing_row = MagicMock()
        existing_row.access_token = enc.encrypt("old-access")
        existing_row.refresh_token = enc.encrypt("existing-refresh")
        existing_row.token_expires_at = None
        existing_row.token_encrypted = True
        existing_row.granted_scopes = []

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_row

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result
        mock_session.flush = AsyncMock()

        store = DatabaseTokenStore(mock_session, enc)

        # Persist tokens with empty refresh_token
        import uuid
        tokens = TokenSet(
            access_token="new-access",
            refresh_token="",  # empty — should not overwrite
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        await store.persist_tokens(uuid.uuid4(), tokens)

        # refresh_token should NOT have been overwritten
        # The row's refresh_token should still be the encrypted "existing-refresh"
        assert existing_row.refresh_token == enc.encrypt("existing-refresh") or \
               enc.decrypt(existing_row.refresh_token) == "existing-refresh"

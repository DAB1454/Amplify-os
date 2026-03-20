"""Tests for adapter_factory module."""

import os
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEPLOYMENT_MODE", "local")
os.environ.setdefault("REDIS_URL", "")


def _make_settings(**overrides):
    from app.config import Settings
    s = Settings()
    defaults = {
        "deployment_mode": "local",
        "token_encryption_key": "",
        "token_encryption_old_keys": [],
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        object.__setattr__(s, k, v)
    return s


class TestAdapterFactory:
    @pytest.mark.asyncio
    async def test_get_adapter_unsupported_platform(self):
        from app.services.adapter_factory import get_adapter

        mock_channel = MagicMock()
        mock_channel.platform = "bandcamp"
        mock_channel.capabilities = {"can_publish": True}

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_channel

        db = AsyncMock()
        db.execute.return_value = mock_result

        settings = _make_settings()

        with pytest.raises(ValueError, match="Unsupported adapter platform"):
            await get_adapter(db, uuid.uuid4(), settings)

    @pytest.mark.asyncio
    async def test_get_adapter_channel_not_found(self):
        from app.services.adapter_factory import get_adapter

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute.return_value = mock_result

        settings = _make_settings()

        with pytest.raises(ValueError, match="not found"):
            await get_adapter(db, uuid.uuid4(), settings)

    @pytest.mark.asyncio
    async def test_get_adapter_channel_not_capable(self):
        from app.services.adapter_factory import get_adapter
        from amplify.adapters.base import AuthenticationError

        mock_channel = MagicMock()
        mock_channel.platform = "instagram"
        mock_channel.capabilities = {"can_publish": False}

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_channel

        db = AsyncMock()
        db.execute.return_value = mock_result

        settings = _make_settings()

        with pytest.raises(AuthenticationError, match="does not have publish capability"):
            await get_adapter(db, uuid.uuid4(), settings)

    @pytest.mark.asyncio
    async def test_get_adapter_dry_run_local(self):
        from app.services.adapter_factory import get_adapter

        mock_channel = MagicMock()
        mock_channel.platform = "instagram"
        mock_channel.capabilities = {"can_publish": True}
        mock_channel.access_token = "test-token"
        mock_channel.refresh_token = ""
        mock_channel.token_expires_at = datetime.utcnow() + timedelta(hours=1)
        mock_channel.platform_account_id = "123"
        mock_channel.settings = {}
        mock_channel.token_encrypted = False
        mock_channel.granted_scopes = []

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_channel

        db = AsyncMock()
        db.execute.return_value = mock_result

        settings = _make_settings(deployment_mode="local")

        import importlib
        with patch.object(importlib, "import_module") as mock_import:
            mock_adapter_class = MagicMock()
            mock_adapter_instance = AsyncMock()
            mock_adapter_instance.dry_run = True
            mock_adapter_class.return_value = mock_adapter_instance
            mock_module = MagicMock()
            setattr(mock_module, "InstagramAdapter", mock_adapter_class)
            mock_import.return_value = mock_module

            adapter = await get_adapter(db, uuid.uuid4(), settings)

        mock_adapter_class.assert_called_once_with(dry_run=True)
        mock_adapter_instance.connect.assert_called_once()

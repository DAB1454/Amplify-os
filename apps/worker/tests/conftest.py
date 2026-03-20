"""Pytest fixtures for worker tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def mock_redis():
    """Provide a mock Redis client for testing."""
    r = AsyncMock()
    r.blpop = AsyncMock(return_value=None)
    r.rpush = AsyncMock()
    r.set = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.aclose = AsyncMock()
    return r


@pytest.fixture
def sample_ingest_calendar_payload():
    return {
        "job_id": "abc123",
        "job_type": "ingest_calendar",
        "payload": {
            "artist_id": "artist_001",
            "platform": "google",
        },
    }


@pytest.fixture
def sample_generate_content_payload():
    return {
        "job_id": "def456",
        "job_type": "generate_content",
        "payload": {
            "campaign_id": "camp_001",
            "content_type": "social_post",
            "platform": "instagram",
        },
    }


@pytest.fixture
def sample_publish_post_payload():
    return {
        "job_id": "ghi789",
        "job_type": "publish_post",
        "payload": {
            "post_id": "post_001",
            "platform": "tiktok",
            "scheduled_at": "2026-04-01T10:00:00Z",
        },
    }

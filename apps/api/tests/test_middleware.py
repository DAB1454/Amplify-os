"""Integration tests for TenantMiddleware behavior.

Tests healthz bypass, local mode injection, and cloud mode JWT validation.
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest

TEST_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
JWT_SECRET = "change-me-in-production"
JWT_ALGORITHM = "HS256"

pytestmark = pytest.mark.asyncio

V1 = "/api/v1"


async def test_healthz_skips_auth(client):
    """GET /healthz returns 200 without any auth headers."""
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


async def test_local_mode_injects_tenant_context(client, seed_tenant):
    """In local mode, requests work without Authorization headers."""
    resp = await client.get(f"{V1}/tenants/me")
    assert resp.status_code == 200
    assert resp.json()["id"] == str(TEST_TENANT_ID)


async def test_cloud_mode_rejects_missing_token(cloud_client, seed_tenant):
    """In cloud mode, requests without Bearer token get 401."""
    resp = await cloud_client.get(f"{V1}/tenants/me")
    assert resp.status_code == 401
    assert "Authorization" in resp.json()["detail"]


async def test_cloud_mode_rejects_invalid_token(cloud_client, seed_tenant):
    """In cloud mode, a random string as token gets 401."""
    resp = await cloud_client.get(
        f"{V1}/tenants/me",
        headers={"Authorization": "Bearer totally-not-a-jwt"},
    )
    assert resp.status_code == 401


async def test_cloud_mode_rejects_expired_token(cloud_client, seed_tenant):
    """In cloud mode, an expired JWT returns 401."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(TEST_USER_ID),
        "tenant_id": str(TEST_TENANT_ID),
        "role": "owner",
        "type": "access",
        "iat": now - timedelta(hours=2),
        "exp": now - timedelta(hours=1),  # expired 1 hour ago
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    resp = await cloud_client.get(
        f"{V1}/tenants/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


async def test_cloud_mode_rejects_refresh_token_as_access(cloud_client, seed_tenant):
    """In cloud mode, using a refresh token for API access returns 401."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(TEST_USER_ID),
        "tenant_id": str(TEST_TENANT_ID),
        "type": "refresh",  # wrong type
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    resp = await cloud_client.get(
        f"{V1}/tenants/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert "token type" in resp.json()["detail"].lower()


async def test_cloud_mode_accepts_valid_token(cloud_client, seed_tenant):
    """In cloud mode, a correctly formed JWT allows access."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(TEST_USER_ID),
        "tenant_id": str(TEST_TENANT_ID),
        "role": "owner",
        "type": "access",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    resp = await cloud_client.get(
        f"{V1}/tenants/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(TEST_TENANT_ID)


async def test_auth_routes_skip_middleware(cloud_client):
    """Auth routes work without Bearer token even in cloud mode."""
    resp = await cloud_client.post(
        f"{V1}/auth/register",
        json={
            "email": "skip@amplify.dev",
            "password": "password1234",
            "tenant_name": "SkipTenant",
        },
    )
    assert resp.status_code == 201, resp.text

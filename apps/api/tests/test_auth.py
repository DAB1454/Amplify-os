"""Integration tests for the auth endpoints.

POST /api/v1/auth/register
POST /api/v1/auth/login
POST /api/v1/auth/refresh
GET  /api/v1/auth/me
"""

import pytest

pytestmark = pytest.mark.asyncio

AUTH = "/api/v1/auth"

REGISTER_PAYLOAD = {
    "email": "newuser@amplify.dev",
    "password": "supersecret123",
    "tenant_name": "My Label",
    "display_name": "New User",
}


# ── Registration ─────────────────────────────────────────────────


async def test_register_creates_user_and_tenant(client):
    """POST /auth/register creates a user + tenant and returns token pair."""
    resp = await client.post(f"{AUTH}/register", json=REGISTER_PAYLOAD)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0


async def test_register_duplicate_email_returns_400(client):
    """Registering the same email twice returns 400."""
    await client.post(f"{AUTH}/register", json=REGISTER_PAYLOAD)
    resp = await client.post(f"{AUTH}/register", json=REGISTER_PAYLOAD)
    assert resp.status_code == 400
    assert "already registered" in resp.json()["detail"].lower()


# ── Login ────────────────────────────────────────────────────────


async def test_login_with_valid_credentials(client):
    """Register then login with correct password succeeds."""
    await client.post(f"{AUTH}/register", json=REGISTER_PAYLOAD)
    resp = await client.post(
        f"{AUTH}/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": REGISTER_PAYLOAD["password"]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data


async def test_login_with_wrong_password_returns_400(client):
    """Login with incorrect password returns 400."""
    await client.post(f"{AUTH}/register", json=REGISTER_PAYLOAD)
    resp = await client.post(
        f"{AUTH}/login",
        json={"email": REGISTER_PAYLOAD["email"], "password": "wrongpassword"},
    )
    assert resp.status_code == 400


async def test_login_with_nonexistent_email_returns_400(client):
    """Login with an unknown email returns 400."""
    resp = await client.post(
        f"{AUTH}/login",
        json={"email": "nobody@amplify.dev", "password": "doesntmatter"},
    )
    assert resp.status_code == 400


# ── Token refresh ────────────────────────────────────────────────


async def test_refresh_token(client):
    """A valid refresh token returns a new token pair."""
    reg = await client.post(f"{AUTH}/register", json=REGISTER_PAYLOAD)
    refresh_token = reg.json()["refresh_token"]

    resp = await client.post(f"{AUTH}/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data


async def test_refresh_with_invalid_token_returns_400(client):
    """An invalid refresh token returns 400."""
    resp = await client.post(f"{AUTH}/refresh", json={"refresh_token": "garbage.token.here"})
    assert resp.status_code == 400


# ── /auth/me ─────────────────────────────────────────────────────


async def test_me_endpoint_local_mode_with_seeded_user(client, seed_tenant):
    """GET /auth/me in local mode returns the seeded user (LOCAL_USER_ID)."""
    resp = await client.get(f"{AUTH}/me")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["email"] == "test@amplify.local"
    assert data["display_name"] == "Test User"
    assert data["is_active"] is True


async def test_me_endpoint_local_mode_no_seed_returns_404(client):
    """GET /auth/me in local mode with no user in DB returns 404.

    The middleware injects LOCAL_USER_ID, but the user doesn't exist.
    """
    resp = await client.get(f"{AUTH}/me")
    assert resp.status_code == 404


async def test_me_endpoint_cloud_mode_returns_401(cloud_client):
    """GET /auth/me in cloud mode returns 401 because auth paths
    skip the JWT middleware (user_id is set to None)."""
    reg = await cloud_client.post(f"{AUTH}/register", json=REGISTER_PAYLOAD)
    assert reg.status_code == 201
    access_token = reg.json()["access_token"]

    # Even with a valid token, the middleware skips auth for /api/v1/auth/*
    # so user_id remains None and the endpoint returns 401
    resp = await cloud_client.get(
        f"{AUTH}/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 401

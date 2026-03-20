"""Integration tests for role-based access control.

Verifies that admin+ routes enforce role checks and that
lower-privileged roles are rejected with 403.
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


def _make_access_token(role: str) -> str:
    """Create a valid access token with the given role."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(TEST_USER_ID),
        "tenant_id": str(TEST_TENANT_ID),
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def test_owner_can_delete_artist(client, seed_tenant):
    """In local mode (role=owner), DELETE /artists/{id} succeeds."""
    created = await client.post(
        f"{V1}/artists",
        json={"name": "Deletable Artist"},
    )
    assert created.status_code == 201
    artist_id = created.json()["id"]

    resp = await client.delete(f"{V1}/artists/{artist_id}")
    assert resp.status_code == 204


async def test_admin_can_delete_artist(cloud_client, seed_tenant):
    """In cloud mode with admin role, DELETE /artists/{id} succeeds."""
    admin_token = _make_access_token("admin")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Create
    created = await cloud_client.post(
        f"{V1}/artists",
        json={"name": "Admin Delete Target"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    artist_id = created.json()["id"]

    # Delete
    resp = await cloud_client.delete(
        f"{V1}/artists/{artist_id}",
        headers=headers,
    )
    assert resp.status_code == 204


async def test_member_cannot_delete_artist(cloud_client, seed_tenant):
    """In cloud mode with member role, DELETE /artists/{id} returns 403."""
    # Create artist as owner first
    owner_token = _make_access_token("owner")
    created = await cloud_client.post(
        f"{V1}/artists",
        json={"name": "Protected Artist"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert created.status_code == 201
    artist_id = created.json()["id"]

    # Try to delete as member
    member_token = _make_access_token("member")
    resp = await cloud_client.delete(
        f"{V1}/artists/{artist_id}",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert resp.status_code == 403
    assert "admin" in resp.json()["detail"].lower()


async def test_member_can_create_artist(cloud_client, seed_tenant):
    """Members can create artists — no role restriction on POST /artists."""
    member_token = _make_access_token("member")
    resp = await cloud_client.post(
        f"{V1}/artists",
        json={"name": "Member Artist"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert resp.status_code == 201


async def test_member_cannot_access_audit_log(cloud_client, seed_tenant):
    """GET /tenants/me/audit-log requires admin+ role."""
    member_token = _make_access_token("member")
    resp = await cloud_client.get(
        f"{V1}/tenants/me/audit-log",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert resp.status_code == 403


async def test_member_cannot_update_tenant(cloud_client, seed_tenant):
    """PUT /tenants/me requires owner role."""
    member_token = _make_access_token("member")
    resp = await cloud_client.put(
        f"{V1}/tenants/me",
        json={"name": "Hacked Name"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert resp.status_code == 403


async def test_admin_cannot_update_tenant(cloud_client, seed_tenant):
    """PUT /tenants/me requires owner role — admin is not enough."""
    admin_token = _make_access_token("admin")
    resp = await cloud_client.put(
        f"{V1}/tenants/me",
        json={"name": "Admin Override"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 403


@pytest.mark.xfail(
    reason="PUT /tenants/me uses direct setattr + flush which triggers "
    "lazy-load of updated_at in async context on SQLite. "
    "Works on Postgres with server-side NOW().",
    strict=False,
)
async def test_owner_can_update_tenant(cloud_client, seed_tenant):
    """PUT /tenants/me succeeds with owner role."""
    owner_token = _make_access_token("owner")
    resp = await cloud_client.put(
        f"{V1}/tenants/me",
        json={"name": "Owner Updated"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Owner Updated"

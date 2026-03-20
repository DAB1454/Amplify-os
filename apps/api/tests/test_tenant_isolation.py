"""Integration tests for tenant-scoped routes.

GET  /api/v1/tenants/me
PUT  /api/v1/tenants/me
GET  /api/v1/tenants/me/members
GET  /api/v1/tenants/me/audit-log
"""

import pytest

pytestmark = pytest.mark.asyncio

V1 = "/api/v1"


async def test_tenant_me_returns_current_tenant(client, seed_tenant):
    """GET /tenants/me returns the tenant profile for the local tenant."""
    resp = await client.get(f"{V1}/tenants/me")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "Test Studio"
    assert data["slug"] == "test-studio"
    assert data["is_active"] is True


@pytest.mark.xfail(
    reason="PUT /tenants/me uses direct setattr + flush which triggers "
    "lazy-load of updated_at in async context on SQLite. "
    "Works on Postgres with server-side NOW().",
    strict=False,
)
async def test_update_tenant(client, seed_tenant):
    """PUT /tenants/me updates the tenant name."""
    resp = await client.put(
        f"{V1}/tenants/me",
        json={"name": "Renamed Studio"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Renamed Studio"

    # Verify persistence
    get_resp = await client.get(f"{V1}/tenants/me")
    assert get_resp.json()["name"] == "Renamed Studio"


async def test_list_members(client, seed_tenant):
    """GET /tenants/me/members returns tenant members."""
    resp = await client.get(f"{V1}/tenants/me/members")
    assert resp.status_code == 200, resp.text
    members = resp.json()
    assert len(members) >= 1
    emails = [m["email"] for m in members]
    assert "test@amplify.local" in emails


async def test_audit_log_records_actions(client, seed_tenant):
    """Creating an artist produces an audit log entry."""
    # Create an artist to generate an audit entry
    await client.post(
        f"{V1}/artists",
        json={"name": "Audit Target", "genre": "rock"},
    )

    resp = await client.get(f"{V1}/tenants/me/audit-log")
    assert resp.status_code == 200, resp.text
    entries = resp.json()
    assert len(entries) >= 1

    actions = [e["action"] for e in entries]
    assert "artist.created" in actions


async def test_audit_log_contains_changes(client, seed_tenant):
    """Audit log entries include the changes dict with correct data."""
    await client.post(
        f"{V1}/artists",
        json={"name": "Change Tracker", "bio": "Test bio", "genre": "jazz"},
    )

    resp = await client.get(f"{V1}/tenants/me/audit-log")
    entries = resp.json()

    # Find the artist.created entry
    create_entry = next(e for e in entries if e["action"] == "artist.created")
    assert create_entry["entity_type"] == "artist"
    assert create_entry["changes"]["name"] == "Change Tracker"
    assert create_entry["changes"]["genre"] == "jazz"
    assert create_entry["entity_id"] is not None

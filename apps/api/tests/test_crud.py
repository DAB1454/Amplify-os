"""Integration tests for CRUD operations on all entities.

Tests run in local mode (no JWT required). The seed_tenant fixture
creates the local-mode tenant + user + owner membership.
"""

import uuid
from datetime import date

import pytest

pytestmark = pytest.mark.asyncio

V1 = "/api/v1"


# ═══════════════════════════════════════════════════════════════════
# ARTISTS
# ═══════════════════════════════════════════════════════════════════


async def test_create_artist(client, seed_tenant):
    """POST /artists creates an artist and auto-generates a slug."""
    resp = await client.post(
        f"{V1}/artists",
        json={"name": "Nova Ray", "bio": "Pop artist", "genre": "pop"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["name"] == "Nova Ray"
    assert data["slug"] == "nova-ray"
    assert data["bio"] == "Pop artist"
    assert "id" in data


async def test_list_artists(client, seed_tenant):
    """GET /artists returns all artists for the tenant."""
    await client.post(f"{V1}/artists", json={"name": "Artist A"})
    await client.post(f"{V1}/artists", json={"name": "Artist B"})

    resp = await client.get(f"{V1}/artists")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_get_artist(client, seed_tenant):
    """GET /artists/{id} returns a single artist."""
    created = await client.post(f"{V1}/artists", json={"name": "Solo Star"})
    artist_id = created.json()["id"]

    resp = await client.get(f"{V1}/artists/{artist_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Solo Star"


async def test_update_artist(client, seed_tenant):
    """PUT /artists/{id} updates the artist."""
    created = await client.post(f"{V1}/artists", json={"name": "Old Name"})
    artist_id = created.json()["id"]

    resp = await client.put(
        f"{V1}/artists/{artist_id}",
        json={"name": "New Name"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


async def test_delete_artist(client, seed_tenant):
    """DELETE /artists/{id} removes the artist (owner role in local mode)."""
    created = await client.post(f"{V1}/artists", json={"name": "Doomed"})
    artist_id = created.json()["id"]

    resp = await client.delete(f"{V1}/artists/{artist_id}")
    assert resp.status_code == 204

    resp = await client.get(f"{V1}/artists/{artist_id}")
    assert resp.status_code == 404


async def test_get_nonexistent_artist_returns_404(client, seed_tenant):
    """GET /artists/{random-uuid} returns 404."""
    fake_id = str(uuid.uuid4())
    resp = await client.get(f"{V1}/artists/{fake_id}")
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════
# RELEASES
# ═══════════════════════════════════════════════════════════════════


async def test_create_release(client, seed_artist):
    """POST /releases creates a release linked to an artist."""
    resp = await client.post(
        f"{V1}/releases",
        json={
            "artist_id": str(seed_artist.id),
            "title": "Midnight EP",
            "release_type": "ep",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["title"] == "Midnight EP"
    assert data["release_type"] == "ep"
    assert data["artist_id"] == str(seed_artist.id)


async def test_list_releases_filtered_by_artist(client, seed_tenant):
    """GET /releases?artist_id= filters releases by artist."""
    # Create two artists
    a1 = await client.post(f"{V1}/artists", json={"name": "Artist 1"})
    a2 = await client.post(f"{V1}/artists", json={"name": "Artist 2"})
    a1_id = a1.json()["id"]
    a2_id = a2.json()["id"]

    # Create releases for each
    await client.post(f"{V1}/releases", json={"artist_id": a1_id, "title": "R1"})
    await client.post(f"{V1}/releases", json={"artist_id": a1_id, "title": "R2"})
    await client.post(f"{V1}/releases", json={"artist_id": a2_id, "title": "R3"})

    resp = await client.get(f"{V1}/releases", params={"artist_id": a1_id})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert all(r["artist_id"] == a1_id for r in data)


# ═══════════════════════════════════════════════════════════════════
# CAMPAIGNS
# ═══════════════════════════════════════════════════════════════════


async def test_create_campaign(client, seed_artist):
    """POST /campaigns creates a campaign for an artist."""
    resp = await client.post(
        f"{V1}/campaigns",
        json={
            "artist_id": str(seed_artist.id),
            "name": "Summer Promo",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["name"] == "Summer Promo"
    assert data["status"] == "draft"


async def test_launch_campaign(client, seed_artist):
    """POST /campaigns/{id}/launch sets status=active."""
    created = await client.post(
        f"{V1}/campaigns",
        json={"artist_id": str(seed_artist.id), "name": "Launch Test"},
    )
    campaign_id = created.json()["id"]

    resp = await client.post(f"{V1}/campaigns/{campaign_id}/launch")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


async def test_pause_campaign(client, seed_artist):
    """POST /campaigns/{id}/pause sets status=paused after launch."""
    created = await client.post(
        f"{V1}/campaigns",
        json={"artist_id": str(seed_artist.id), "name": "Pause Test"},
    )
    campaign_id = created.json()["id"]

    await client.post(f"{V1}/campaigns/{campaign_id}/launch")
    resp = await client.post(f"{V1}/campaigns/{campaign_id}/pause")
    assert resp.status_code == 200
    assert resp.json()["status"] == "paused"


async def test_list_campaigns_filtered_by_status(client, seed_artist):
    """GET /campaigns?status= filters campaigns by status."""
    # Create and launch one campaign, leave another as draft
    c1 = await client.post(
        f"{V1}/campaigns",
        json={"artist_id": str(seed_artist.id), "name": "Draft Camp"},
    )
    c2 = await client.post(
        f"{V1}/campaigns",
        json={"artist_id": str(seed_artist.id), "name": "Active Camp"},
    )
    await client.post(f"{V1}/campaigns/{c2.json()['id']}/launch")

    resp = await client.get(f"{V1}/campaigns", params={"status": "active"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Active Camp"


# ═══════════════════════════════════════════════════════════════════
# CALENDAR ITEMS
# ═══════════════════════════════════════════════════════════════════


async def test_create_calendar_item(client, seed_tenant):
    """POST /calendar creates a calendar item."""
    resp = await client.post(
        f"{V1}/calendar",
        json={
            "title": "Release Day",
            "item_type": "milestone",
            "scheduled_date": "2025-06-01",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["title"] == "Release Day"
    assert data["item_type"] == "milestone"
    assert data["is_completed"] is False


async def test_complete_calendar_item(client, seed_tenant):
    """PATCH /calendar/{id}/complete marks item as completed."""
    created = await client.post(
        f"{V1}/calendar",
        json={
            "title": "Submit artwork",
            "item_type": "deadline",
            "scheduled_date": "2025-05-15",
        },
    )
    item_id = created.json()["id"]

    resp = await client.patch(f"{V1}/calendar/{item_id}/complete")
    assert resp.status_code == 200
    assert resp.json()["is_completed"] is True


async def test_filter_calendar_by_date_range(client, seed_tenant):
    """GET /calendar?start_date=&end_date= filters by date range."""
    await client.post(
        f"{V1}/calendar",
        json={"title": "Early", "item_type": "reminder", "scheduled_date": "2025-01-01"},
    )
    await client.post(
        f"{V1}/calendar",
        json={"title": "Mid", "item_type": "reminder", "scheduled_date": "2025-06-15"},
    )
    await client.post(
        f"{V1}/calendar",
        json={"title": "Late", "item_type": "reminder", "scheduled_date": "2025-12-31"},
    )

    resp = await client.get(
        f"{V1}/calendar",
        params={"start_date": "2025-06-01", "end_date": "2025-06-30"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["title"] == "Mid"


# ═══════════════════════════════════════════════════════════════════
# POSTS
# ═══════════════════════════════════════════════════════════════════


async def test_create_post(client, seed_channel):
    """POST /posts creates a post linked to a channel."""
    resp = await client.post(
        f"{V1}/posts",
        json={
            "channel_id": str(seed_channel.id),
            "platform": "instagram",
            "content_text": "New music coming soon!",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["platform"] == "instagram"
    assert data["content_text"] == "New music coming soon!"
    assert data["status"] == "draft"


async def test_list_posts_filtered_by_platform(client, seed_channel):
    """GET /posts?platform= filters posts by platform."""
    await client.post(
        f"{V1}/posts",
        json={"channel_id": str(seed_channel.id), "platform": "instagram", "content_text": "IG post"},
    )
    await client.post(
        f"{V1}/posts",
        json={"channel_id": str(seed_channel.id), "platform": "tiktok", "content_text": "TT post"},
    )

    resp = await client.get(f"{V1}/posts", params={"platform": "instagram"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["platform"] == "instagram"


async def test_update_post(client, seed_channel):
    """PUT /posts/{id} updates the post content."""
    created = await client.post(
        f"{V1}/posts",
        json={"channel_id": str(seed_channel.id), "platform": "instagram", "content_text": "v1"},
    )
    post_id = created.json()["id"]

    resp = await client.put(
        f"{V1}/posts/{post_id}",
        json={"content_text": "v2 updated"},
    )
    assert resp.status_code == 200
    assert resp.json()["content_text"] == "v2 updated"

"""Tests for the release destination layer.

Covers:
- Release destination URL fields (CRUD)
- Post destination_url field
- UTM campaign URL generation
- Redirect / smart link CRUD + click tracking
- Destination validation (campaign posts without URLs)
- Canonical CTA URL resolution
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

TEST_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")

pytestmark = pytest.mark.asyncio


# ── Helpers ──────────────────────────────────────────────────────


async def _create_artist(client: AsyncClient) -> dict:
    resp = await client.post(
        "/api/v1/artists/",
        json={"name": "Luna Vega", "genre": "electronic"},
    )
    assert resp.status_code == 201
    return resp.json()


async def _create_release(client: AsyncClient, artist_id: str, **extras) -> dict:
    payload = {
        "artist_id": artist_id,
        "title": "Midnight Frequencies",
        "release_type": "album",
        "release_date": "2026-03-29",
        **extras,
    }
    resp = await client.post("/api/v1/releases/", json=payload)
    assert resp.status_code == 201
    return resp.json()


async def _create_campaign(
    client: AsyncClient, artist_id: str, release_id: str | None = None
) -> dict:
    payload = {
        "artist_id": artist_id,
        "name": "Album Launch",
        "release_id": release_id,
    }
    resp = await client.post("/api/v1/campaigns/", json=payload)
    assert resp.status_code == 201
    return resp.json()


async def _create_channel(db_session, artist_id: str) -> uuid.UUID:
    from amplify.db.models.channel import ChannelConnectionModel

    channel = ChannelConnectionModel(
        id=uuid.uuid4(),
        tenant_id=TEST_TENANT_ID,
        artist_id=uuid.UUID(artist_id),
        platform="instagram",
        display_name="@lunavega",
        is_active=True,
    )
    db_session.add(channel)
    await db_session.commit()
    return channel.id


async def _create_post(
    client: AsyncClient,
    campaign_id: str,
    channel_id: str,
    destination_url: str | None = None,
) -> dict:
    payload = {
        "campaign_id": campaign_id,
        "channel_id": channel_id,
        "platform": "instagram",
        "content_text": "New music out now!",
    }
    if destination_url:
        payload["destination_url"] = destination_url
    resp = await client.post("/api/v1/posts/", json=payload)
    assert resp.status_code == 201
    return resp.json()


# ── Release destination URL tests ────────────────────────────────


class TestReleaseDestinations:
    async def test_create_release_with_destinations(self, client, seed_tenant):
        artist = await _create_artist(client)
        release = await _create_release(
            client,
            artist["id"],
            hyperfollow_url="https://distrokid.com/hyperfollow/lunavega/midnight",
            linktree_url="https://linktr.ee/lunavega",
            bandcamp_url="https://lunavega.bandcamp.com/album/midnight",
            youtube_url="https://youtube.com/@lunavega",
            tiktok_url="https://tiktok.com/@lunavega",
            instagram_url="https://instagram.com/lunavega",
        )
        assert release["hyperfollow_url"] == "https://distrokid.com/hyperfollow/lunavega/midnight"
        assert release["linktree_url"] == "https://linktr.ee/lunavega"
        assert release["bandcamp_url"] == "https://lunavega.bandcamp.com/album/midnight"
        assert release["youtube_url"] == "https://youtube.com/@lunavega"
        assert release["tiktok_url"] == "https://tiktok.com/@lunavega"
        assert release["instagram_url"] == "https://instagram.com/lunavega"

    async def test_create_release_destinations_default_null(self, client, seed_tenant):
        artist = await _create_artist(client)
        release = await _create_release(client, artist["id"])
        assert release["hyperfollow_url"] is None
        assert release["linktree_url"] is None

    async def test_update_release_destinations(self, client, seed_tenant):
        artist = await _create_artist(client)
        release = await _create_release(client, artist["id"])
        resp = await client.put(
            f"/api/v1/releases/{release['id']}",
            json={"hyperfollow_url": "https://distrokid.com/hyperfollow/test"},
        )
        assert resp.status_code == 200
        assert resp.json()["hyperfollow_url"] == "https://distrokid.com/hyperfollow/test"

    async def test_get_release_includes_destinations(self, client, seed_tenant):
        artist = await _create_artist(client)
        release = await _create_release(
            client,
            artist["id"],
            bandcamp_url="https://lunavega.bandcamp.com",
        )
        resp = await client.get(f"/api/v1/releases/{release['id']}")
        assert resp.status_code == 200
        assert resp.json()["bandcamp_url"] == "https://lunavega.bandcamp.com"


# ── Post destination_url tests ───────────────────────────────────


class TestPostDestination:
    async def test_create_post_with_destination(self, client, db_session, seed_tenant):
        artist = await _create_artist(client)
        channel_id = await _create_channel(db_session, artist["id"])
        campaign = await _create_campaign(client, artist["id"])
        post = await _create_post(
            client,
            campaign["id"],
            str(channel_id),
            destination_url="https://distrokid.com/hyperfollow/test",
        )
        assert post["destination_url"] == "https://distrokid.com/hyperfollow/test"

    async def test_create_post_destination_default_null(self, client, db_session, seed_tenant):
        artist = await _create_artist(client)
        channel_id = await _create_channel(db_session, artist["id"])
        campaign = await _create_campaign(client, artist["id"])
        post = await _create_post(client, campaign["id"], str(channel_id))
        assert post["destination_url"] is None

    async def test_update_post_destination(self, client, db_session, seed_tenant):
        artist = await _create_artist(client)
        channel_id = await _create_channel(db_session, artist["id"])
        campaign = await _create_campaign(client, artist["id"])
        post = await _create_post(client, campaign["id"], str(channel_id))
        resp = await client.put(
            f"/api/v1/posts/{post['id']}",
            json={"destination_url": "https://linktr.ee/lunavega"},
        )
        assert resp.status_code == 200
        assert resp.json()["destination_url"] == "https://linktr.ee/lunavega"


# ── UTM builder tests ───────────────────────────────────────────


class TestUTMBuilder:
    def test_build_url_basic(self):
        from amplify.adapters.shortlinks.utm import UTMBuilder

        url = UTMBuilder.build_url(
            "https://example.com",
            source="instagram",
            medium="social",
            campaign="album-launch",
        )
        assert "utm_source=instagram" in url
        assert "utm_medium=social" in url
        assert "utm_campaign=album-launch" in url

    def test_build_url_with_content_and_term(self):
        from amplify.adapters.shortlinks.utm import UTMBuilder

        url = UTMBuilder.build_url(
            "https://example.com",
            source="tiktok",
            medium="social",
            campaign="test",
            content="variant-a",
            term="hook",
        )
        assert "utm_content=variant-a" in url
        assert "utm_term=hook" in url

    def test_build_url_preserves_existing_params(self):
        from amplify.adapters.shortlinks.utm import UTMBuilder

        url = UTMBuilder.build_url(
            "https://example.com?ref=test",
            source="ig",
            medium="social",
            campaign="c",
        )
        assert "ref=test" in url
        assert "utm_source=ig" in url

    def test_build_campaign_url(self):
        from amplify.adapters.shortlinks.utm import UTMBuilder

        url = UTMBuilder.build_campaign_url(
            destination_url="https://linktr.ee/lunavega",
            campaign_name="Album Launch 2026",
            platform="instagram",
        )
        assert "utm_source=instagram" in url
        assert "utm_medium=social" in url
        assert "utm_campaign=album-launch-2026" in url

    def test_build_campaign_url_email_medium(self):
        from amplify.adapters.shortlinks.utm import UTMBuilder

        url = UTMBuilder.build_campaign_url(
            destination_url="https://linktr.ee/lunavega",
            campaign_name="launch",
            platform="email",
        )
        assert "utm_medium=email" in url

    def test_build_campaign_urls_batch(self):
        from amplify.adapters.shortlinks.utm import UTMBuilder

        destinations = {
            "hyperfollow_url": "https://distrokid.com/hyperfollow/test",
            "linktree_url": "https://linktr.ee/test",
            "bandcamp_url": None,
        }
        result = UTMBuilder.build_campaign_urls(
            destinations=destinations,
            campaign_name="test",
            platform="tiktok",
        )
        assert "hyperfollow_url" in result
        assert "linktree_url" in result
        assert "bandcamp_url" not in result  # None values skipped


# ── Redirect / smart link tests ──────────────────────────────────


class TestRedirects:
    async def test_create_redirect(self, client, seed_tenant):
        resp = await client.post(
            "/api/v1/links/",
            json={"target_url": "https://linktr.ee/lunavega", "slug": "luna"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["slug"] == "luna"
        assert data["target_url"] == "https://linktr.ee/lunavega"
        assert data["click_count"] == 0

    async def test_create_redirect_auto_slug(self, client, seed_tenant):
        resp = await client.post(
            "/api/v1/links/",
            json={"target_url": "https://example.com"},
        )
        assert resp.status_code == 201
        assert len(resp.json()["slug"]) == 8

    async def test_duplicate_slug_rejected(self, client, seed_tenant):
        await client.post(
            "/api/v1/links/",
            json={"target_url": "https://a.com", "slug": "dup"},
        )
        resp = await client.post(
            "/api/v1/links/",
            json={"target_url": "https://b.com", "slug": "dup"},
        )
        assert resp.status_code == 409

    async def test_list_redirects(self, client, seed_tenant):
        await client.post(
            "/api/v1/links/",
            json={"target_url": "https://a.com", "slug": "list1"},
        )
        await client.post(
            "/api/v1/links/",
            json={"target_url": "https://b.com", "slug": "list2"},
        )
        resp = await client.get("/api/v1/links/")
        assert resp.status_code == 200
        assert len(resp.json()) >= 2

    async def test_get_redirect_stats(self, client, seed_tenant):
        await client.post(
            "/api/v1/links/",
            json={"target_url": "https://a.com", "slug": "stats1"},
        )
        resp = await client.get("/api/v1/links/stats1/stats")
        assert resp.status_code == 200
        assert resp.json()["click_count"] == 0

    async def test_resolve_redirect_increments_clicks(self, client, seed_tenant):
        await client.post(
            "/api/v1/links/",
            json={"target_url": "https://example.com/dest", "slug": "go"},
        )
        # Resolve (follow_redirects=False to check the 307)
        ac = AsyncClient(
            transport=ASGITransport(app=client._transport.app),
            base_url="http://test",
            follow_redirects=False,
        )
        async with ac:
            resp = await ac.get("/r/go")
        assert resp.status_code == 307
        assert resp.headers["location"] == "https://example.com/dest"

        # Click count should be 1
        resp = await client.get("/api/v1/links/go/stats")
        assert resp.json()["click_count"] == 1

    async def test_resolve_redirect_not_found(self, client, seed_tenant):
        ac = AsyncClient(
            transport=ASGITransport(app=client._transport.app),
            base_url="http://test",
            follow_redirects=False,
        )
        async with ac:
            resp = await ac.get("/r/nonexistent")
        assert resp.status_code == 404

    async def test_delete_redirect(self, client, seed_tenant):
        await client.post(
            "/api/v1/links/",
            json={"target_url": "https://a.com", "slug": "del1"},
        )
        resp = await client.delete("/api/v1/links/del1")
        assert resp.status_code == 204

        resp = await client.get("/api/v1/links/del1/stats")
        assert resp.status_code == 404


# ── Destination validation tests ─────────────────────────────────


class TestCampaignDestinations:
    async def test_destinations_report_all_linked(self, client, db_session, seed_tenant):
        artist = await _create_artist(client)
        release = await _create_release(
            client,
            artist["id"],
            hyperfollow_url="https://distrokid.com/hyperfollow/test",
        )
        campaign = await _create_campaign(client, artist["id"], release["id"])
        channel_id = await _create_channel(db_session, artist["id"])
        await _create_post(
            client,
            campaign["id"],
            str(channel_id),
            destination_url="https://distrokid.com/hyperfollow/test",
        )

        resp = await client.get(f"/api/v1/campaigns/{campaign['id']}/destinations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["canonical_url"] == "https://distrokid.com/hyperfollow/test"
        assert data["total_posts"] == 1
        assert data["posts_with_destination"] == 1
        assert data["posts_missing_destination"] == []

    async def test_destinations_report_missing(self, client, db_session, seed_tenant):
        artist = await _create_artist(client)
        release = await _create_release(
            client,
            artist["id"],
            hyperfollow_url="https://distrokid.com/hyperfollow/test",
        )
        campaign = await _create_campaign(client, artist["id"], release["id"])
        channel_id = await _create_channel(db_session, artist["id"])
        # Post without destination_url
        await _create_post(client, campaign["id"], str(channel_id))

        resp = await client.get(f"/api/v1/campaigns/{campaign['id']}/destinations")
        assert resp.status_code == 200
        data = resp.json()
        assert data["posts_with_destination"] == 0
        assert len(data["posts_missing_destination"]) == 1
        assert data["posts_missing_destination"][0]["platform"] == "instagram"

    async def test_canonical_url_priority(self, client, db_session, seed_tenant):
        """Canonical URL should prefer hyperfollow > linktree > bandcamp."""
        artist = await _create_artist(client)
        release = await _create_release(
            client,
            artist["id"],
            linktree_url="https://linktr.ee/test",
            bandcamp_url="https://test.bandcamp.com",
        )
        campaign = await _create_campaign(client, artist["id"], release["id"])

        resp = await client.get(f"/api/v1/campaigns/{campaign['id']}/destinations")
        data = resp.json()
        # linktree_url should win over bandcamp_url (2nd in priority)
        assert data["canonical_url"] == "https://linktr.ee/test"

    async def test_canonical_url_no_release(self, client, db_session, seed_tenant):
        """Campaign without a linked release should have null canonical URL."""
        artist = await _create_artist(client)
        campaign = await _create_campaign(client, artist["id"])

        resp = await client.get(f"/api/v1/campaigns/{campaign['id']}/destinations")
        data = resp.json()
        assert data["canonical_url"] is None

    async def test_destinations_campaign_not_found(self, client, seed_tenant):
        fake_id = str(uuid.uuid4())
        resp = await client.get(f"/api/v1/campaigns/{fake_id}/destinations")
        assert resp.status_code == 404

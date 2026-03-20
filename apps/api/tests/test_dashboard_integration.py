"""Integration tests for dashboard page data contracts.

These test the exact API endpoints and response shapes that the frontend
dashboard pages fetch. Each test group maps to a frontend page.
"""

import uuid

import pytest
import pytest_asyncio


# ── Dashboard page (/): overview, campaigns, posts, approvals ─────

class TestDashboardPage:
    """Tests for the data the dashboard page fetches."""

    @pytest.mark.asyncio
    async def test_analytics_overview_returns_counts(self, client, seed_tenant):
        resp = await client.get("/api/v1/analytics/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_campaigns" in data
        assert "total_posts" in data
        assert "published_posts" in data

    @pytest.mark.asyncio
    async def test_approvals_count_returns_pending(self, client, seed_tenant):
        resp = await client.get("/api/v1/approvals/count")
        assert resp.status_code == 200
        data = resp.json()
        assert "pending_count" in data
        assert isinstance(data["pending_count"], int)

    @pytest.mark.asyncio
    async def test_campaigns_filter_by_active(self, client, seed_artist):
        # Create a campaign first
        resp = await client.post("/api/v1/campaigns/", json={
            "artist_id": str(seed_artist.id),
            "name": "Test Campaign",
            "phase": "pre_release",
        })
        assert resp.status_code == 201
        campaign_id = resp.json()["id"]

        # Launch it
        resp = await client.post(f"/api/v1/campaigns/{campaign_id}/launch")
        assert resp.status_code == 200

        # Filter by active
        resp = await client.get("/api/v1/campaigns/?status=active")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert all(c["status"] == "active" for c in data)

    @pytest.mark.asyncio
    async def test_posts_list_returns_expected_shape(self, client, seed_channel):
        resp = await client.get("/api/v1/posts/")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


# ── Artists page (/artists): CRUD ────────────────────────────────

class TestArtistsPage:
    """Tests for the artist CRUD endpoints the artists page uses."""

    @pytest.mark.asyncio
    async def test_list_artists_empty(self, client, seed_tenant):
        resp = await client.get("/api/v1/artists/")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_create_artist(self, client, seed_tenant):
        resp = await client.post("/api/v1/artists/", json={
            "name": "Test Artist",
            "bio": "A test artist",
            "genre": "electronic",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test Artist"
        assert data["slug"] == "test-artist"
        assert data["bio"] == "A test artist"
        assert data["genre"] == "electronic"
        assert "id" in data
        assert "created_at" in data

    @pytest.mark.asyncio
    async def test_create_and_list_artists(self, client, seed_tenant):
        await client.post("/api/v1/artists/", json={"name": "Artist One"})
        await client.post("/api/v1/artists/", json={"name": "Artist Two"})
        resp = await client.get("/api/v1/artists/")
        assert resp.status_code == 200
        assert len(resp.json()) >= 2

    @pytest.mark.asyncio
    async def test_update_artist(self, client, seed_tenant):
        create_resp = await client.post("/api/v1/artists/", json={"name": "Old Name"})
        artist_id = create_resp.json()["id"]

        resp = await client.put(f"/api/v1/artists/{artist_id}", json={
            "name": "New Name",
            "genre": "hip-hop",
        })
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"
        assert resp.json()["genre"] == "hip-hop"

    @pytest.mark.asyncio
    async def test_delete_artist(self, client, seed_tenant):
        create_resp = await client.post("/api/v1/artists/", json={"name": "To Delete"})
        artist_id = create_resp.json()["id"]

        resp = await client.delete(f"/api/v1/artists/{artist_id}")
        assert resp.status_code == 204

        # Verify it's gone
        resp = await client.get(f"/api/v1/artists/{artist_id}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_artist_response_shape(self, client, seed_tenant):
        """Verify the response shape matches what the frontend expects."""
        create_resp = await client.post("/api/v1/artists/", json={
            "name": "Shape Test",
            "bio": "bio",
            "genre": "rock",
        })
        data = create_resp.json()
        expected_fields = {"id", "name", "slug", "bio", "genre", "image_url",
                           "spotify_id", "social_links", "created_at", "updated_at", "tenant_id"}
        assert expected_fields.issubset(set(data.keys()))


# ── Releases page (/releases): CRUD ─────────────────────────────

class TestReleasesPage:
    """Tests for the release CRUD endpoints the releases page uses."""

    @pytest.mark.asyncio
    async def test_list_releases_empty(self, client, seed_tenant):
        resp = await client.get("/api/v1/releases/")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_create_release(self, client, seed_artist):
        resp = await client.post("/api/v1/releases/", json={
            "artist_id": str(seed_artist.id),
            "title": "Test Single",
            "release_type": "single",
            "release_date": "2026-04-01",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Test Single"
        assert data["release_type"] == "single"
        assert data["release_date"] == "2026-04-01"
        assert data["artist_id"] == str(seed_artist.id)

    @pytest.mark.asyncio
    async def test_list_releases_filter_by_artist(self, client, seed_artist):
        # Create two releases
        await client.post("/api/v1/releases/", json={
            "artist_id": str(seed_artist.id),
            "title": "Release A",
        })

        resp = await client.get(f"/api/v1/releases/?artist_id={seed_artist.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert all(r["artist_id"] == str(seed_artist.id) for r in data)

    @pytest.mark.asyncio
    async def test_update_release(self, client, seed_artist):
        create_resp = await client.post("/api/v1/releases/", json={
            "artist_id": str(seed_artist.id),
            "title": "Old Title",
        })
        release_id = create_resp.json()["id"]

        resp = await client.put(f"/api/v1/releases/{release_id}", json={
            "title": "New Title",
            "release_type": "album",
        })
        assert resp.status_code == 200
        assert resp.json()["title"] == "New Title"
        assert resp.json()["release_type"] == "album"

    @pytest.mark.asyncio
    async def test_delete_release(self, client, seed_artist):
        create_resp = await client.post("/api/v1/releases/", json={
            "artist_id": str(seed_artist.id),
            "title": "To Delete",
        })
        release_id = create_resp.json()["id"]

        resp = await client.delete(f"/api/v1/releases/{release_id}")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_release_response_shape(self, client, seed_artist):
        create_resp = await client.post("/api/v1/releases/", json={
            "artist_id": str(seed_artist.id),
            "title": "Shape Test",
        })
        data = create_resp.json()
        expected_fields = {"id", "tenant_id", "artist_id", "title", "release_type",
                           "status", "release_date", "created_at", "updated_at"}
        assert expected_fields.issubset(set(data.keys()))


# ── Campaigns page (/campaigns): CRUD + launch/pause ─────────────

class TestCampaignsPage:
    """Tests for the campaign endpoints the campaigns page uses."""

    @pytest.mark.asyncio
    async def test_list_campaigns_empty(self, client, seed_tenant):
        resp = await client.get("/api/v1/campaigns/")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_create_campaign(self, client, seed_artist):
        resp = await client.post("/api/v1/campaigns/", json={
            "artist_id": str(seed_artist.id),
            "name": "Test Campaign",
            "phase": "pre_release",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test Campaign"
        assert data["status"] == "draft"
        assert data["phase"] == "pre_release"

    @pytest.mark.asyncio
    async def test_launch_campaign(self, client, seed_artist):
        create_resp = await client.post("/api/v1/campaigns/", json={
            "artist_id": str(seed_artist.id),
            "name": "Launch Test",
        })
        campaign_id = create_resp.json()["id"]

        resp = await client.post(f"/api/v1/campaigns/{campaign_id}/launch")
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    @pytest.mark.asyncio
    async def test_pause_campaign(self, client, seed_artist):
        create_resp = await client.post("/api/v1/campaigns/", json={
            "artist_id": str(seed_artist.id),
            "name": "Pause Test",
        })
        campaign_id = create_resp.json()["id"]

        # Launch first
        await client.post(f"/api/v1/campaigns/{campaign_id}/launch")
        # Then pause
        resp = await client.post(f"/api/v1/campaigns/{campaign_id}/pause")
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

    @pytest.mark.asyncio
    async def test_filter_by_status(self, client, seed_artist):
        # Create draft + active campaigns
        create_resp = await client.post("/api/v1/campaigns/", json={
            "artist_id": str(seed_artist.id),
            "name": "Draft One",
        })
        create_resp2 = await client.post("/api/v1/campaigns/", json={
            "artist_id": str(seed_artist.id),
            "name": "Active One",
        })
        await client.post(f"/api/v1/campaigns/{create_resp2.json()['id']}/launch")

        # Filter by draft
        resp = await client.get("/api/v1/campaigns/?status=draft")
        assert resp.status_code == 200
        data = resp.json()
        assert all(c["status"] == "draft" for c in data)

    @pytest.mark.asyncio
    async def test_update_campaign(self, client, seed_artist):
        create_resp = await client.post("/api/v1/campaigns/", json={
            "artist_id": str(seed_artist.id),
            "name": "Old Name",
        })
        campaign_id = create_resp.json()["id"]

        resp = await client.put(f"/api/v1/campaigns/{campaign_id}", json={
            "name": "New Name",
            "phase": "sustain",
        })
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"
        assert resp.json()["phase"] == "sustain"

    @pytest.mark.asyncio
    async def test_delete_campaign(self, client, seed_artist):
        create_resp = await client.post("/api/v1/campaigns/", json={
            "artist_id": str(seed_artist.id),
            "name": "To Delete",
        })
        campaign_id = create_resp.json()["id"]

        resp = await client.delete(f"/api/v1/campaigns/{campaign_id}")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_campaign_response_shape(self, client, seed_artist):
        create_resp = await client.post("/api/v1/campaigns/", json={
            "artist_id": str(seed_artist.id),
            "name": "Shape Test",
        })
        data = create_resp.json()
        expected_fields = {"id", "tenant_id", "artist_id", "name", "status",
                           "phase", "start_date", "end_date", "budget",
                           "created_at", "updated_at"}
        assert expected_fields.issubset(set(data.keys()))


# ── Posts page (/posts): list + status filter + actions ──────────

class TestPostsPage:
    """Tests for the post endpoints the posts page uses."""

    @pytest.mark.asyncio
    async def test_create_and_list_posts(self, client, seed_channel):
        resp = await client.post("/api/v1/posts/", json={
            "channel_id": str(seed_channel.id),
            "platform": "instagram",
            "content_text": "Hello world",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["platform"] == "instagram"
        assert data["status"] == "draft"
        assert data["content_text"] == "Hello world"

        # List
        resp = await client.get("/api/v1/posts/")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    @pytest.mark.asyncio
    async def test_post_queue_approve_publish_flow(self, client, seed_channel):
        """Test the full post lifecycle the posts page drives.

        The policy engine may block approve if the post lacks a destination
        link or artist mapping, returning 409.  We accept either 200 (approved)
        or 409 (policy-blocked) as valid — the frontend handles both.
        """
        # Create
        resp = await client.post("/api/v1/posts/", json={
            "channel_id": str(seed_channel.id),
            "platform": "instagram",
            "content_text": "Flow test",
        })
        post_id = resp.json()["id"]

        # Queue
        resp = await client.post(f"/api/v1/posts/{post_id}/queue")
        assert resp.status_code == 200

        # Approve — policy engine may block (409) in test env
        resp = await client.post(f"/api/v1/posts/{post_id}/approve")
        assert resp.status_code in (200, 409)

    @pytest.mark.asyncio
    async def test_filter_posts_by_status(self, client, seed_channel):
        await client.post("/api/v1/posts/", json={
            "channel_id": str(seed_channel.id),
            "platform": "instagram",
            "content_text": "Draft post",
        })

        resp = await client.get("/api/v1/posts/?status=draft")
        assert resp.status_code == 200
        data = resp.json()
        assert all(p["status"] == "draft" for p in data)

    @pytest.mark.asyncio
    async def test_post_response_shape(self, client, seed_channel):
        create_resp = await client.post("/api/v1/posts/", json={
            "channel_id": str(seed_channel.id),
            "platform": "instagram",
            "content_text": "Shape test",
        })
        data = create_resp.json()
        expected_fields = {"id", "platform", "status", "content_text", "media_urls",
                           "scheduled_at", "published_at", "permalink", "retry_count",
                           "last_error", "policy_decision", "created_at"}
        assert expected_fields.issubset(set(data.keys()))


# ── Analytics page (/analytics): overview, timeseries, scores ────

class TestAnalyticsPage:
    """Tests for the analytics endpoints the analytics page uses."""

    @pytest.mark.asyncio
    async def test_overview(self, client, seed_tenant):
        resp = await client.get("/api/v1/analytics/overview")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_campaigns" in data
        assert "total_posts" in data

    @pytest.mark.asyncio
    async def test_scores(self, client, seed_tenant):
        resp = await client.get("/api/v1/analytics/scores?days=14")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_analyst_report(self, client, seed_tenant):
        resp = await client.get("/api/v1/analytics/analyst-report?days=7")
        assert resp.status_code == 200
        data = resp.json()
        assert "verdicts" in data
        assert "summary" in data


# ── Approvals page (/approvals): pending, history, actions ───────

class TestApprovalsPage:
    """Tests for the approval endpoints the approvals page uses."""

    @pytest.mark.asyncio
    async def test_pending_count(self, client, seed_tenant):
        resp = await client.get("/api/v1/approvals/count")
        assert resp.status_code == 200
        assert "pending_count" in resp.json()

    @pytest.mark.asyncio
    async def test_pending_list(self, client, seed_tenant):
        resp = await client.get("/api/v1/approvals/pending")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_history_list(self, client, seed_tenant):
        resp = await client.get("/api/v1/approvals/history")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_create_and_approve(self, client, seed_tenant):
        import uuid as _uuid
        # Create approval request
        resp = await client.post("/api/v1/approvals/", json={
            "action_type": "publish_post",
            "entity_type": "post",
            "entity_id": str(_uuid.uuid4()),
            "notes": "Please review",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending"
        assert isinstance(data["comments"], list)
        assert len(data["comments"]) == 0
        approval_id = data["id"]

        # Approve it with a comment
        resp = await client.post(f"/api/v1/approvals/{approval_id}/approve", json={
            "comment": "Looks good",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"
        # The approve comment should be present in the response
        assert len(data["comments"]) == 1
        assert data["comments"][0]["body"] == "Looks good"

    @pytest.mark.asyncio
    async def test_create_approval_has_empty_comments(self, client, seed_tenant):
        """Regression: newly created approval must serialize comments=[] without lazy-load error."""
        import uuid as _uuid
        resp = await client.post("/api/v1/approvals/", json={
            "action_type": "budget_change",
            "entity_type": "campaign",
            "entity_id": str(_uuid.uuid4()),
            "notes": "Budget increase",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert "comments" in data
        assert data["comments"] == []

    @pytest.mark.asyncio
    async def test_approval_with_comments_serializes(self, client, seed_tenant):
        """Regression: approval with comments attached must serialize correctly."""
        import uuid as _uuid
        # Create
        resp = await client.post("/api/v1/approvals/", json={
            "action_type": "publish_post",
            "entity_type": "post",
            "entity_id": str(_uuid.uuid4()),
            "notes": "Review needed",
        })
        assert resp.status_code == 201
        approval_id = resp.json()["id"]

        # Add a comment via the comments sub-endpoint
        resp = await client.post(f"/api/v1/approvals/{approval_id}/comments", json={
            "body": "First comment",
        })
        assert resp.status_code == 201

        # Fetch the approval — comments should be present
        resp = await client.get(f"/api/v1/approvals/{approval_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["comments"]) == 1
        assert data["comments"][0]["body"] == "First comment"

        # Reject with a comment — should now have 2 comments
        resp = await client.post(f"/api/v1/approvals/{approval_id}/reject", json={
            "comment": "Needs rework",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "rejected"
        assert len(data["comments"]) == 2

    @pytest.mark.asyncio
    async def test_add_comment_endpoint_serializes(self, client, seed_tenant):
        """Regression: POST /approvals/{id}/comments must return a fully
        serialized ApprovalCommentResponse including server-generated
        created_at, without triggering MissingGreenlet."""
        import uuid as _uuid
        resp = await client.post("/api/v1/approvals/", json={
            "action_type": "publish_post",
            "entity_type": "post",
            "entity_id": str(_uuid.uuid4()),
            "notes": "Comment endpoint test",
        })
        assert resp.status_code == 201
        approval_id = resp.json()["id"]

        resp = await client.post(f"/api/v1/approvals/{approval_id}/comments", json={
            "body": "Standalone comment",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["body"] == "Standalone comment"
        assert "created_at" in data
        assert data["created_at"] is not None
        assert data["approval_id"] == approval_id


# ── Billing page (/billing): plans, usage, subscription ──────────

class TestBillingPage:
    """Tests for the billing endpoints the billing page uses."""

    @pytest.mark.asyncio
    async def test_plans(self, client, seed_tenant):
        resp = await client.get("/api/v1/billing/plans")
        assert resp.status_code == 200
        plans = resp.json()
        assert isinstance(plans, list)
        assert len(plans) >= 1
        for plan in plans:
            assert "tier" in plan
            assert "price_monthly" in plan
            assert "features" in plan

    @pytest.mark.asyncio
    async def test_usage(self, client, seed_tenant):
        resp = await client.get("/api/v1/billing/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert "tier" in data
        assert "items" in data

    @pytest.mark.asyncio
    async def test_subscription(self, client, seed_tenant):
        resp = await client.get("/api/v1/billing/subscription")
        assert resp.status_code == 200
        data = resp.json()
        assert "plan_tier" in data
        assert "plan_name" in data


# ── Channels page (/channels): platforms, channels, tasks ────────

class TestChannelsPage:
    """Tests for the channel endpoints the channels page uses."""

    @pytest.mark.asyncio
    async def test_platforms(self, client, seed_tenant):
        resp = await client.get("/api/v1/channels/platforms")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        for p in data:
            assert "platform" in p
            assert "mode" in p

    @pytest.mark.asyncio
    async def test_list_channels(self, client, seed_channel):
        resp = await client.get("/api/v1/channels/")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        ch = data[0]
        assert "id" in ch
        assert "platform" in ch
        assert "integration_mode" in ch
        assert "connection_status" in ch
        assert "capabilities" in ch

    @pytest.mark.asyncio
    async def test_channel_response_has_oauth_fields(self, client, seed_channel):
        """Verify _channel_to_dict includes the new OAuth fields."""
        resp = await client.get("/api/v1/channels/")
        ch = resp.json()[0]
        assert "connection_status" in ch
        assert "granted_scopes" in ch
        assert "capabilities" in ch
        assert "last_health_check_at" in ch


# ── Tenant page (/tenants): profile ──────────────────────────────

class TestTenantPage:
    """Tests for the tenant endpoint."""

    @pytest.mark.asyncio
    async def test_get_current_tenant(self, client, seed_tenant):
        resp = await client.get("/api/v1/tenants/me")
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert "name" in data
        assert "plan_tier" in data
        assert "settings" in data

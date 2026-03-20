"""Tests for learning event capture — idempotency, replay safety, and validation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.learning import LearningEventModel

# Fixed test IDs matching TenantMiddleware local mode
TEST_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Service-level tests ─────────────────────────────────────────


class TestLearningEventServiceEmit:
    """Tests for LearningEventService.emit()."""

    @pytest.mark.asyncio
    async def test_emit_basic_event(self, db_session: AsyncSession, seed_tenant):
        from app.services.learning_event_service import LearningEventService

        svc = LearningEventService(db_session, TEST_TENANT_ID)
        event = await svc.emit(
            action_type="post.created",
            observed_at=_now(),
            post_id=uuid.uuid4(),
            platform="instagram",
            source="test",
        )
        assert event.action_type == "post.created"
        assert event.tenant_id == TEST_TENANT_ID
        assert event.platform == "instagram"
        assert event.idempotency_key is not None

    @pytest.mark.asyncio
    async def test_emit_auto_generates_idempotency_key(self, db_session: AsyncSession, seed_tenant):
        from app.services.learning_event_service import LearningEventService

        svc = LearningEventService(db_session, TEST_TENANT_ID)
        post_id = uuid.uuid4()
        now = _now()
        event = await svc.emit(
            action_type="post.published",
            observed_at=now,
            post_id=post_id,
        )
        expected_key = f"post.published:{post_id}:{now.isoformat()}"
        assert event.idempotency_key == expected_key

    @pytest.mark.asyncio
    async def test_emit_with_explicit_idempotency_key(self, db_session: AsyncSession, seed_tenant):
        from app.services.learning_event_service import LearningEventService

        svc = LearningEventService(db_session, TEST_TENANT_ID)
        event = await svc.emit(
            action_type="test.event",
            observed_at=_now(),
            idempotency_key="my-custom-key-123",
        )
        assert event.idempotency_key == "my-custom-key-123"


class TestDuplicateEventHandling:
    """Tests for idempotent event ingestion."""

    @pytest.mark.asyncio
    async def test_duplicate_idempotency_key_returns_existing(self, db_session: AsyncSession, seed_tenant):
        from app.services.learning_event_service import LearningEventService

        svc = LearningEventService(db_session, TEST_TENANT_ID)
        key = "dedup-test-key-001"

        event1 = await svc.emit(
            action_type="post.created",
            observed_at=_now(),
            idempotency_key=key,
            source="first",
        )
        event2 = await svc.emit(
            action_type="post.created",
            observed_at=_now(),
            idempotency_key=key,
            source="second",  # Different source — should still return event1
        )

        assert event1.id == event2.id
        assert event2.source == "first"  # Original event preserved

    @pytest.mark.asyncio
    async def test_duplicate_creates_only_one_row(self, db_session: AsyncSession, seed_tenant):
        from app.services.learning_event_service import LearningEventService

        svc = LearningEventService(db_session, TEST_TENANT_ID)
        key = "dedup-count-key-001"

        await svc.emit(action_type="test.a", observed_at=_now(), idempotency_key=key)
        await svc.emit(action_type="test.b", observed_at=_now(), idempotency_key=key)
        await svc.emit(action_type="test.c", observed_at=_now(), idempotency_key=key)

        result = await db_session.execute(
            select(LearningEventModel).where(
                LearningEventModel.idempotency_key == key
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_different_keys_create_separate_events(self, db_session: AsyncSession, seed_tenant):
        from app.services.learning_event_service import LearningEventService

        svc = LearningEventService(db_session, TEST_TENANT_ID)

        e1 = await svc.emit(
            action_type="post.created",
            observed_at=_now(),
            idempotency_key="unique-key-a",
        )
        e2 = await svc.emit(
            action_type="post.created",
            observed_at=_now(),
            idempotency_key="unique-key-b",
        )
        assert e1.id != e2.id


class TestReplaySafety:
    """Verify that replaying the same events is safe and idempotent."""

    @pytest.mark.asyncio
    async def test_replay_batch_is_idempotent(self, db_session: AsyncSession, seed_tenant):
        from app.services.learning_event_service import LearningEventService

        svc = LearningEventService(db_session, TEST_TENANT_ID)

        batch = [
            {
                "action_type": "post.created",
                "observed_at": _now(),
                "idempotency_key": "replay-1",
                "source": "backfill",
            },
            {
                "action_type": "post.published",
                "observed_at": _now(),
                "idempotency_key": "replay-2",
                "source": "backfill",
            },
        ]

        # First ingest
        r1 = await svc.ingest_batch(batch, source="backfill")
        assert r1["created"] == 2
        assert r1["errors"] == []

        # Replay — same batch again
        r2 = await svc.ingest_batch(batch, source="backfill")
        # Events already exist, so emit() returns them without error.
        # The count still reads "created" but the DB has no duplicates.
        assert r2["errors"] == []

        # Verify only 2 rows exist
        result = await db_session.execute(
            select(LearningEventModel).where(
                LearningEventModel.idempotency_key.in_(["replay-1", "replay-2"])
            )
        )
        assert len(result.scalars().all()) == 2

    @pytest.mark.asyncio
    async def test_replay_preserves_original_data(self, db_session: AsyncSession, seed_tenant):
        from app.services.learning_event_service import LearningEventService

        svc = LearningEventService(db_session, TEST_TENANT_ID)
        key = "preserve-original"

        original = await svc.emit(
            action_type="post.published",
            observed_at=_now(),
            idempotency_key=key,
            platform="instagram",
            features={"original": True},
        )

        replayed = await svc.emit(
            action_type="post.published",
            observed_at=_now(),
            idempotency_key=key,
            platform="tiktok",  # Different platform
            features={"replayed": True},
        )

        # Should return original, not overwrite
        assert replayed.id == original.id
        assert replayed.platform == "instagram"
        assert replayed.features == {"original": True}


class TestMalformedPayloadRejection:
    """Tests for invalid payloads via the ingest batch path."""

    @pytest.mark.asyncio
    async def test_missing_action_type(self, db_session: AsyncSession, seed_tenant):
        from app.services.learning_event_service import LearningEventService

        svc = LearningEventService(db_session, TEST_TENANT_ID)
        result = await svc.ingest_batch([
            {"observed_at": _now()},  # Missing action_type
        ])
        assert result["errors"] != []
        assert result["created"] == 0

    @pytest.mark.asyncio
    async def test_invalid_uuid_in_post_id(self, db_session: AsyncSession, seed_tenant):
        from app.services.learning_event_service import LearningEventService

        svc = LearningEventService(db_session, TEST_TENANT_ID)
        result = await svc.ingest_batch([
            {
                "action_type": "test",
                "observed_at": _now(),
                "post_id": "not-a-uuid",
            },
        ])
        assert result["errors"] != []

    @pytest.mark.asyncio
    async def test_valid_events_in_mixed_batch(self, db_session: AsyncSession, seed_tenant):
        from app.services.learning_event_service import LearningEventService

        svc = LearningEventService(db_session, TEST_TENANT_ID)
        result = await svc.ingest_batch([
            {"action_type": "good.event", "observed_at": _now(), "idempotency_key": "mixed-good"},
            {"observed_at": _now()},  # Bad — missing action_type
            {"action_type": "also.good", "observed_at": _now(), "idempotency_key": "mixed-good2"},
        ])
        assert result["created"] == 2
        assert len(result["errors"]) == 1
        assert result["errors"][0]["index"] == 1


# ── API endpoint tests ──────────────────────────────────────────


class TestIngestEndpoint:
    """Tests for POST /api/v1/learning/events/ingest."""

    @pytest.mark.asyncio
    async def test_ingest_single_event(self, client, seed_tenant):
        resp = await client.post("/api/v1/learning/events/ingest", json={
            "events": [{
                "action_type": "post.created",
                "observed_at": _now().isoformat(),
                "platform": "instagram",
                "idempotency_key": "api-ingest-1",
            }],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["created"] == 1
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_ingest_duplicate_via_api(self, client, seed_tenant):
        payload = {
            "events": [{
                "action_type": "post.published",
                "observed_at": _now().isoformat(),
                "idempotency_key": "api-dedup-1",
            }],
        }
        r1 = await client.post("/api/v1/learning/events/ingest", json=payload)
        assert r1.status_code == 200

        r2 = await client.post("/api/v1/learning/events/ingest", json=payload)
        assert r2.status_code == 200
        # No errors on replay
        assert r2.json()["errors"] == []

    @pytest.mark.asyncio
    async def test_ingest_empty_batch_rejected(self, client, seed_tenant):
        resp = await client.post("/api/v1/learning/events/ingest", json={
            "events": [],
        })
        assert resp.status_code == 422  # Pydantic validation: min_length=1

    @pytest.mark.asyncio
    async def test_ingest_missing_action_type_rejected(self, client, seed_tenant):
        resp = await client.post("/api/v1/learning/events/ingest", json={
            "events": [{
                "observed_at": _now().isoformat(),
            }],
        })
        assert resp.status_code == 422  # Pydantic validation: action_type required

    @pytest.mark.asyncio
    async def test_emit_single_event_endpoint(self, client, seed_tenant):
        resp = await client.post("/api/v1/learning/events", json={
            "action_type": "post.approved",
            "observed_at": _now().isoformat(),
            "idempotency_key": "single-emit-1",
            "platform": "tiktok",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["action_type"] == "post.approved"
        assert data["idempotency_key"] == "single-emit-1"

    @pytest.mark.asyncio
    async def test_list_events_endpoint(self, client, seed_tenant):
        # Create some events first
        for i in range(3):
            await client.post("/api/v1/learning/events", json={
                "action_type": "post.created",
                "observed_at": _now().isoformat(),
                "idempotency_key": f"list-test-{i}",
            })

        resp = await client.get("/api/v1/learning/events?action_type=post.created")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 3


# ── Capture helper tests ────────────────────────────────────────


class TestCaptureHelpers:
    """Tests for the worker-safe capture helper functions."""

    def test_post_created_helper(self):
        from amplify.learning.capture import post_created

        event = post_created(
            post_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            platform="instagram",
            content_length=150,
            has_media=True,
        )
        assert event["action_type"] == "post.created"
        assert event["platform"] == "instagram"
        assert event["features"]["content_length"] == 150
        assert event["features"]["has_media"] is True
        assert event["idempotency_key"].startswith("post.created:")

    def test_post_published_helper(self):
        from amplify.learning.capture import post_published

        event = post_published(
            post_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            platform="tiktok",
            platform_post_id="tiktok_123",
            permalink="https://tiktok.com/@user/123",
        )
        assert event["action_type"] == "post.published"
        assert event["features"]["platform_post_id"] == "tiktok_123"

    def test_post_failed_helper(self):
        from amplify.learning.capture import post_failed

        event = post_failed(
            post_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            platform="youtube",
            error="Rate limited",
            retry_count=2,
            is_permanent=False,
        )
        assert event["action_type"] == "post.failed"
        assert event["features"]["retry_count"] == 2
        assert event["features"]["is_permanent"] is False

    def test_post_failed_permanent_helper(self):
        from amplify.learning.capture import post_failed

        event = post_failed(
            post_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            platform="instagram",
            error="Account suspended",
            retry_count=3,
            is_permanent=True,
        )
        assert event["action_type"] == "post.failed_permanent"

    def test_policy_evaluated_helper(self):
        from amplify.learning.capture import policy_evaluated

        event = policy_evaluated(
            post_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            platform="instagram",
            decision="block",
            blocking_reasons=["duplicate caption"],
        )
        assert event["action_type"] == "policy.evaluated"
        assert event["features"]["decision"] == "block"
        assert "duplicate caption" in event["features"]["blocking_reasons"]

    def test_metrics_synced_helper(self):
        from amplify.learning.capture import metrics_synced

        event = metrics_synced(
            post_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            platform="instagram",
            measurement_window="24h",
            metrics={"impressions": 5000, "engagement_rate": 0.045},
        )
        assert event["action_type"] == "metrics.synced"
        assert event["outcomes"]["impressions"] == 5000
        assert event["features"]["measurement_window"] == "24h"

    def test_agent_decision_helper(self):
        from amplify.learning.capture import agent_decision

        event = agent_decision(
            tenant_id=uuid.uuid4(),
            decision_type="content_selection",
            post_id=uuid.uuid4(),
            platform="instagram",
            decision="carousel",
            reasoning="Carousels have 2x engagement for this artist",
        )
        assert event["action_type"] == "agent.content_selection"
        assert event["features"]["decision"] == "carousel"

    def test_helpers_produce_safe_replay_keys(self):
        """Each helper generates a deterministic idempotency key."""
        from amplify.learning.capture import post_created, post_published

        pid = uuid.uuid4()
        tid = uuid.uuid4()
        e1 = post_created(post_id=pid, tenant_id=tid, platform="ig")
        e2 = post_published(post_id=pid, tenant_id=tid, platform="ig")

        # Different action types → different keys
        assert e1["idempotency_key"] != e2["idempotency_key"]
        # Keys contain the post_id for traceability
        assert str(pid) in e1["idempotency_key"]

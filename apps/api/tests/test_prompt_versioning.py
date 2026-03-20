"""Tests for prompt version service — DB-backed CRUD, assignment, rollback, lineage.

Uses the same SQLite in-memory test infrastructure as other API tests.
Service imports are deferred to work with pytest importlib mode.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure apps/api is on the path for import resolution
_api_root = Path(__file__).resolve().parent.parent
if str(_api_root) not in sys.path:
    sys.path.insert(0, str(_api_root))

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DEPLOYMENT_MODE", "local")
os.environ.setdefault("REDIS_URL", "")

from amplify.db.base import Base  # noqa: E402
import amplify.db.models  # noqa: E402, F401
from amplify.db.models.learning import (  # noqa: E402
    LearningAuditLogModel,
    LearningEventModel,
    PromptAssignmentModel,
    PromptVersionModel,
)
from amplify.db.models.tenant import TenantModel  # noqa: E402


def _json_serializer(obj):
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _dumps(value):
    return json.dumps(value, default=_json_serializer)


TEST_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
TEST_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


# ── DB fixtures (self-contained) ─────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False, json_serializer=_dumps)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(
        bind=db_engine, class_=AsyncSession, expire_on_commit=False,
    )
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def tenant(db_session: AsyncSession):
    t = TenantModel(
        id=TEST_TENANT_ID, name="Test Tenant", slug="test-tenant", plan_tier="free",
    )
    db_session.add(t)
    await db_session.commit()
    return t


@pytest_asyncio.fixture
async def svc(db_session: AsyncSession, tenant):
    from app.services.prompt_version_service import PromptVersionService
    return PromptVersionService(db_session, TEST_TENANT_ID)


# ═══════════════════════════════════════════════════════════════════
# Version CRUD
# ═══════════════════════════════════════════════════════════════════


class TestVersionCRUD:
    async def test_create_version_auto_increments(self, svc):
        v1 = await svc.create_version(
            prompt_key="planner_system", template="v1 template",
        )
        v2 = await svc.create_version(
            prompt_key="planner_system", template="v2 template",
        )
        assert v1.version == 1
        assert v2.version == 2

    async def test_create_version_different_keys_independent(self, svc):
        va = await svc.create_version(
            prompt_key="planner_system", template="planner v1",
        )
        vb = await svc.create_version(
            prompt_key="content_caption", template="content v1",
        )
        assert va.version == 1
        assert vb.version == 1

    async def test_get_version(self, svc):
        created = await svc.create_version(
            prompt_key="test_key", template="hello",
        )
        fetched = await svc.get_version(created.id)
        assert fetched is not None
        assert fetched.template == "hello"

    async def test_get_version_not_found(self, svc):
        result = await svc.get_version(uuid.uuid4())
        assert result is None

    async def test_list_versions_filter_by_key(self, svc):
        await svc.create_version(prompt_key="planner_system", template="p1")
        await svc.create_version(prompt_key="content_caption", template="c1")
        versions = await svc.list_versions("planner_system")
        assert len(versions) == 1
        assert versions[0].prompt_key == "planner_system"

    async def test_list_versions_excludes_inactive(self, svc):
        v = await svc.create_version(prompt_key="k", template="t")
        v.is_active = False
        await svc.db.flush()
        versions = await svc.list_versions("k")
        assert len(versions) == 0

    async def test_list_versions_include_inactive(self, svc):
        v = await svc.create_version(prompt_key="k", template="t")
        v.is_active = False
        await svc.db.flush()
        versions = await svc.list_versions("k", include_inactive=True)
        assert len(versions) == 1

    async def test_get_latest_active(self, svc):
        await svc.create_version(prompt_key="k", template="v1")
        await svc.create_version(prompt_key="k", template="v2")
        latest = await svc.get_latest_active("k")
        assert latest is not None
        assert latest.version == 2

    async def test_create_with_metadata(self, svc):
        v = await svc.create_version(
            prompt_key="planner_system",
            template="You are a planner.",
            system_prompt="Full system prompt here.",
            model_id="claude-sonnet-4-20250514",
            parameters={"temperature": 0.0, "max_tokens": 4096},
            description="Initial planner prompt",
            author="drew",
        )
        assert v.model_id == "claude-sonnet-4-20250514"
        assert v.parameters["temperature"] == 0.0
        assert v.author == "drew"


# ═══════════════════════════════════════════════════════════════════
# Promote / Deprecate / Rollback
# ═══════════════════════════════════════════════════════════════════


class TestPromoteDeprecateRollback:
    async def test_promote_deprecates_previous(self, svc):
        v1 = await svc.create_version(prompt_key="k", template="v1")
        v2 = await svc.create_version(prompt_key="k", template="v2")
        # Both start active; promote v2 explicitly
        await svc.promote(v2.id)
        await svc.db.flush()

        # v1 should be deprecated
        refreshed_v1 = await svc.get_version(v1.id)
        assert refreshed_v1 is not None
        assert not refreshed_v1.is_active

        refreshed_v2 = await svc.get_version(v2.id)
        assert refreshed_v2 is not None
        assert refreshed_v2.is_active

    async def test_promote_nonexistent_raises(self, svc):
        with pytest.raises(ValueError, match="not found"):
            await svc.promote(uuid.uuid4())

    async def test_deprecate_version(self, svc):
        v = await svc.create_version(prompt_key="k", template="t")
        assert v.is_active
        await svc.deprecate(v.id)
        refreshed = await svc.get_version(v.id)
        assert refreshed is not None
        assert not refreshed.is_active

    async def test_deprecate_nonexistent_raises(self, svc):
        with pytest.raises(ValueError, match="not found"):
            await svc.deprecate(uuid.uuid4())

    async def test_rollback_to_previous(self, svc):
        v1 = await svc.create_version(prompt_key="k", template="v1")
        v2 = await svc.create_version(prompt_key="k", template="v2")

        # Rollback — should activate v1 and deactivate v2
        rolled = await svc.rollback("k")
        assert rolled.version == 1
        assert rolled.is_active

        # v2 should be inactive
        v2_refreshed = await svc.get_version(v2.id)
        assert v2_refreshed is not None
        assert not v2_refreshed.is_active

    async def test_rollback_to_specific_version(self, svc):
        v1 = await svc.create_version(prompt_key="k", template="v1")
        v2 = await svc.create_version(prompt_key="k", template="v2")
        v3 = await svc.create_version(prompt_key="k", template="v3")

        rolled = await svc.rollback("k", target_version=1)
        assert rolled.version == 1
        assert rolled.is_active

    async def test_rollback_nonexistent_key_raises(self, svc):
        with pytest.raises(ValueError, match="No versions found"):
            await svc.rollback("nonexistent")

    async def test_rollback_single_version_raises(self, svc):
        await svc.create_version(prompt_key="k", template="only one")
        with pytest.raises(ValueError, match="Only one version"):
            await svc.rollback("k")

    async def test_rollback_nonexistent_target_raises(self, svc):
        await svc.create_version(prompt_key="k", template="v1")
        await svc.create_version(prompt_key="k", template="v2")
        with pytest.raises(ValueError, match="Version 99 not found"):
            await svc.rollback("k", target_version=99)


# ═══════════════════════════════════════════════════════════════════
# Assignment logic
# ═══════════════════════════════════════════════════════════════════


class TestAssignments:
    async def test_assign_creates_assignment(self, svc):
        v = await svc.create_version(prompt_key="k", template="t")
        a = await svc.assign(
            tenant_id=TEST_TENANT_ID,
            prompt_key="k",
            version_id=v.id,
            reason="A/B test variant",
        )
        assert a.tenant_id == TEST_TENANT_ID
        assert a.prompt_version_id == v.id
        assert a.reason == "A/B test variant"
        assert a.is_active

    async def test_assign_upserts_in_place(self, svc):
        v1 = await svc.create_version(prompt_key="k", template="v1")
        v2 = await svc.create_version(prompt_key="k", template="v2")

        a1 = await svc.assign(TEST_TENANT_ID, "k", v1.id, reason="first")
        a2 = await svc.assign(TEST_TENANT_ID, "k", v2.id, reason="second")

        # Assignment should be updated to point at v2
        refreshed = await svc.get_assignment(TEST_TENANT_ID, "k")
        assert refreshed is not None
        assert refreshed.prompt_version_id == v2.id
        assert refreshed.reason == "second"

    async def test_unassign(self, svc):
        v = await svc.create_version(prompt_key="k", template="t")
        await svc.assign(TEST_TENANT_ID, "k", v.id)
        removed = await svc.unassign(TEST_TENANT_ID, "k")
        assert removed is True
        assert await svc.get_assignment(TEST_TENANT_ID, "k") is None

    async def test_unassign_nonexistent_returns_false(self, svc):
        assert await svc.unassign(TEST_TENANT_ID, "nonexistent") is False

    async def test_list_assignments(self, svc):
        v1 = await svc.create_version(prompt_key="k1", template="t1")
        v2 = await svc.create_version(prompt_key="k2", template="t2")
        await svc.assign(TEST_TENANT_ID, "k1", v1.id)
        await svc.assign(TEST_TENANT_ID, "k2", v2.id)
        assignments = await svc.list_assignments(TEST_TENANT_ID)
        assert len(assignments) == 2


# ═══════════════════════════════════════════════════════════════════
# Registry builder
# ═══════════════════════════════════════════════════════════════════


class TestRegistryBuilder:
    async def test_build_registry(self, svc):
        v = await svc.create_version(prompt_key="planner_system", template="template")
        await svc.assign(TEST_TENANT_ID, "planner_system", v.id)
        await svc.db.flush()

        registry = await svc.build_registry(TEST_TENANT_ID, ["planner_system"])

        # Check versions
        latest = registry.get_latest_active("planner_system")
        assert latest is not None
        assert latest.version == 1

        # Check assignment
        assignment = registry.get_assignment(str(TEST_TENANT_ID), "planner_system")
        assert assignment is not None

    async def test_build_registry_inactive_excluded(self, svc):
        v = await svc.create_version(prompt_key="k", template="t")
        v.is_active = False
        await svc.db.flush()

        registry = await svc.build_registry(TEST_TENANT_ID, ["k"])
        assert registry.get_latest_active("k") is None


# ═══════════════════════════════════════════════════════════════════
# Lineage persistence
# ═══════════════════════════════════════════════════════════════════


class TestLineagePersistence:
    async def test_lineage_stored_in_learning_event(self, svc):
        """Lineage metadata stored in learning event features is queryable."""
        lineage_data = {
            "prompt_lineage": {
                "prompt_version_id": str(uuid.uuid4()),
                "prompt_key": "planner_system",
                "prompt_version_number": 2,
                "agent_name": "planner",
                "run_id": str(uuid.uuid4()),
            },
        }

        post_id = uuid.uuid4()
        event = LearningEventModel(
            id=uuid.uuid4(),
            tenant_id=TEST_TENANT_ID,
            action_type="post_published",
            post_id=post_id,
            features=lineage_data,
            outcomes={},
            reward=0.65,
            observed_at=datetime.now(timezone.utc),
            source="test",
            confidence=1.0,
            schema_version=1,
        )
        svc.db.add(event)
        await svc.db.flush()

        lineage = await svc.get_lineage_for_post(post_id)
        assert len(lineage) == 1
        assert lineage[0]["prompt_lineage"] is not None
        assert lineage[0]["prompt_lineage"]["prompt_key"] == "planner_system"
        assert lineage[0]["reward"] == 0.65

    async def test_lineage_empty_for_no_events(self, svc):
        lineage = await svc.get_lineage_for_post(uuid.uuid4())
        assert lineage == []

    async def test_version_outcome_stats(self, svc):
        v = await svc.create_version(prompt_key="k", template="t")
        # Create some events with rewards
        for reward in [0.5, 0.7, 0.9]:
            svc.db.add(LearningEventModel(
                id=uuid.uuid4(),
                tenant_id=TEST_TENANT_ID,
                action_type="post_published",
                features={},
                outcomes={},
                reward=reward,
                observed_at=datetime.now(timezone.utc),
                source="test",
                confidence=1.0,
                schema_version=1,
            ))
        await svc.db.flush()

        stats = await svc.get_version_outcome_stats("k", v.id)
        assert stats["event_count"] == 3
        assert abs(stats["avg_reward"] - 0.7) < 0.01


# ═══════════════════════════════════════════════════════════════════
# Audit trail
# ═══════════════════════════════════════════════════════════════════


class TestAuditTrail:
    async def test_create_version_audited(self, svc):
        await svc.create_version(prompt_key="k", template="t", author="drew")
        await svc.db.flush()

        from sqlalchemy import select
        result = await svc.db.execute(
            select(LearningAuditLogModel).where(
                LearningAuditLogModel.action == "prompt_version.created"
            )
        )
        logs = list(result.scalars().all())
        assert len(logs) == 1
        assert logs[0].details["author"] == "drew"

    async def test_promote_audited(self, svc):
        v = await svc.create_version(prompt_key="k", template="t")
        await svc.promote(v.id)
        await svc.db.flush()

        from sqlalchemy import select
        result = await svc.db.execute(
            select(LearningAuditLogModel).where(
                LearningAuditLogModel.action == "prompt_version.promoted"
            )
        )
        logs = list(result.scalars().all())
        assert len(logs) == 1

    async def test_rollback_audited(self, svc):
        await svc.create_version(prompt_key="k", template="v1")
        await svc.create_version(prompt_key="k", template="v2")
        await svc.rollback("k")
        await svc.db.flush()

        from sqlalchemy import select
        result = await svc.db.execute(
            select(LearningAuditLogModel).where(
                LearningAuditLogModel.action == "prompt_version.rolled_back"
            )
        )
        logs = list(result.scalars().all())
        assert len(logs) == 1
        assert logs[0].details["rolled_back_to_version"] == 1

    async def test_assignment_audited(self, svc):
        v = await svc.create_version(prompt_key="k", template="t")
        await svc.assign(TEST_TENANT_ID, "k", v.id, reason="shadow test")
        await svc.db.flush()

        from sqlalchemy import select
        result = await svc.db.execute(
            select(LearningAuditLogModel).where(
                LearningAuditLogModel.action == "prompt_assignment.created"
            )
        )
        logs = list(result.scalars().all())
        assert len(logs) == 1
        assert logs[0].details["reason"] == "shadow test"

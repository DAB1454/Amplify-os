"""Tests for assisted task domain models and workflow templates."""

from __future__ import annotations

import pytest

from amplify.core.domain.assisted_task import (
    AssistedTask,
    AssistedTaskCreate,
    ChecklistItem,
    TaskPriority,
    TaskStatus,
    URLValidation,
    bandcamp_release_checklist,
    bandcamp_update_checklist,
    linktree_new_release_checklist,
    linktree_sync_checklist,
)
from amplify.core.domain.post import (
    IntegrationMode,
    Platform,
    PLATFORM_INTEGRATION_MODE,
    PLATFORM_METADATA,
)


# ── Integration Mode ────────────────────────────────────────────


class TestIntegrationMode:
    def test_instagram_is_automatic(self):
        assert PLATFORM_INTEGRATION_MODE[Platform.INSTAGRAM] == IntegrationMode.AUTOMATIC

    def test_tiktok_is_automatic(self):
        assert PLATFORM_INTEGRATION_MODE[Platform.TIKTOK] == IntegrationMode.AUTOMATIC

    def test_youtube_is_automatic(self):
        assert PLATFORM_INTEGRATION_MODE[Platform.YOUTUBE] == IntegrationMode.AUTOMATIC

    def test_bandcamp_is_assisted(self):
        assert PLATFORM_INTEGRATION_MODE[Platform.BANDCAMP] == IntegrationMode.ASSISTED

    def test_linktree_is_assisted(self):
        assert PLATFORM_INTEGRATION_MODE[Platform.LINKTREE] == IntegrationMode.ASSISTED

    def test_all_platforms_have_mode(self):
        for platform in Platform:
            assert platform in PLATFORM_INTEGRATION_MODE

    def test_all_platforms_have_metadata(self):
        for platform in Platform:
            assert platform in PLATFORM_METADATA
            meta = PLATFORM_METADATA[platform]
            assert "label" in meta
            assert "mode" in meta
            assert "description" in meta
            assert "capabilities" in meta

    def test_metadata_mode_matches_integration_mode(self):
        for platform in Platform:
            meta_mode = PLATFORM_METADATA[platform]["mode"]
            enum_mode = PLATFORM_INTEGRATION_MODE[platform].value
            assert meta_mode == enum_mode


# ── Checklist Templates ─────────────────────────────────────────


class TestBandcampChecklists:
    def test_release_checklist_has_items(self):
        items = bandcamp_release_checklist()
        assert len(items) == 10

    def test_release_checklist_all_pending(self):
        items = bandcamp_release_checklist()
        assert all(not item.is_completed for item in items)

    def test_release_checklist_unique_keys(self):
        items = bandcamp_release_checklist()
        keys = [item.key for item in items]
        assert len(keys) == len(set(keys))

    def test_release_checklist_has_publish_step(self):
        items = bandcamp_release_checklist()
        keys = {item.key for item in items}
        assert "publish" in keys
        assert "verify_url" in keys

    def test_update_checklist_has_items(self):
        items = bandcamp_update_checklist()
        assert len(items) == 3
        keys = {item.key for item in items}
        assert "verify_changes" in keys


class TestLinktreeChecklists:
    def test_sync_checklist_has_items(self):
        items = linktree_sync_checklist()
        assert len(items) == 7

    def test_sync_checklist_unique_keys(self):
        items = linktree_sync_checklist()
        keys = [item.key for item in items]
        assert len(keys) == len(set(keys))

    def test_sync_checklist_has_verify_step(self):
        items = linktree_sync_checklist()
        keys = {item.key for item in items}
        assert "test_links" in keys

    def test_new_release_checklist_has_items(self):
        items = linktree_new_release_checklist()
        assert len(items) == 6
        keys = {item.key for item in items}
        assert "generate_link" in keys
        assert "verify_tracking" in keys


# ── AssistedTask Model ──────────────────────────────────────────


class TestAssistedTask:
    def test_defaults(self):
        from uuid import uuid4
        task = AssistedTask(
            tenant_id=uuid4(),
            platform="bandcamp",
            title="Publish on Bandcamp",
        )
        assert task.status == TaskStatus.PENDING
        assert task.priority == TaskPriority.MEDIUM
        assert task.checklist == []
        assert task.is_checklist_complete is True  # empty checklist

    def test_checklist_progress(self):
        from uuid import uuid4
        task = AssistedTask(
            tenant_id=uuid4(),
            platform="bandcamp",
            title="Test",
            checklist=[
                ChecklistItem(key="a", label="Step A", is_completed=True),
                ChecklistItem(key="b", label="Step B", is_completed=False),
                ChecklistItem(key="c", label="Step C", is_completed=True),
            ],
        )
        done, total = task.checklist_progress
        assert done == 2
        assert total == 3

    def test_is_checklist_complete_false(self):
        from uuid import uuid4
        task = AssistedTask(
            tenant_id=uuid4(),
            platform="linktree",
            title="Sync",
            checklist=[
                ChecklistItem(key="a", label="Step A", is_completed=True),
                ChecklistItem(key="b", label="Step B", is_completed=False),
            ],
        )
        assert task.is_checklist_complete is False

    def test_is_checklist_complete_true(self):
        from uuid import uuid4
        task = AssistedTask(
            tenant_id=uuid4(),
            platform="linktree",
            title="Sync",
            checklist=[
                ChecklistItem(key="a", label="Step A", is_completed=True),
                ChecklistItem(key="b", label="Step B", is_completed=True),
            ],
        )
        assert task.is_checklist_complete is True


class TestURLValidation:
    def test_valid_result(self):
        v = URLValidation(
            url="https://artist.bandcamp.com/album/test",
            is_valid=True,
            status_code=200,
            title="Test Album | Artist",
        )
        assert v.is_valid
        assert v.error is None

    def test_invalid_result(self):
        v = URLValidation(
            url="https://artist.bandcamp.com/album/missing",
            is_valid=False,
            status_code=404,
            error="HTTP 404",
        )
        assert not v.is_valid
        assert v.error == "HTTP 404"


class TestAssistedTaskCreate:
    def test_create_with_checklist(self):
        from uuid import uuid4
        data = AssistedTaskCreate(
            platform="bandcamp",
            title="Release on Bandcamp",
            checklist=bandcamp_release_checklist(),
            release_id=uuid4(),
        )
        assert len(data.checklist) == 10
        assert data.priority == TaskPriority.MEDIUM

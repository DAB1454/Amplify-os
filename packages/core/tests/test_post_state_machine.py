"""Tests for the post state machine transitions."""

from __future__ import annotations

import pytest

from amplify.core.domain.post import PostStatus, VALID_TRANSITIONS, Post


class TestValidTransitions:
    def test_draft_can_become_queued(self):
        assert PostStatus.QUEUED in VALID_TRANSITIONS[PostStatus.DRAFT]

    def test_draft_can_become_scheduled(self):
        assert PostStatus.SCHEDULED in VALID_TRANSITIONS[PostStatus.DRAFT]

    def test_draft_cannot_become_published(self):
        assert PostStatus.PUBLISHED not in VALID_TRANSITIONS[PostStatus.DRAFT]

    def test_queued_can_become_approved(self):
        assert PostStatus.APPROVED in VALID_TRANSITIONS[PostStatus.QUEUED]

    def test_queued_can_go_back_to_draft(self):
        assert PostStatus.DRAFT in VALID_TRANSITIONS[PostStatus.QUEUED]

    def test_approved_can_become_publishing(self):
        assert PostStatus.PUBLISHING in VALID_TRANSITIONS[PostStatus.APPROVED]

    def test_approved_can_become_scheduled(self):
        assert PostStatus.SCHEDULED in VALID_TRANSITIONS[PostStatus.APPROVED]

    def test_scheduled_can_become_publishing(self):
        assert PostStatus.PUBLISHING in VALID_TRANSITIONS[PostStatus.SCHEDULED]

    def test_publishing_can_become_published(self):
        assert PostStatus.PUBLISHED in VALID_TRANSITIONS[PostStatus.PUBLISHING]

    def test_publishing_can_become_failed(self):
        assert PostStatus.FAILED in VALID_TRANSITIONS[PostStatus.PUBLISHING]

    def test_published_is_terminal(self):
        assert len(VALID_TRANSITIONS[PostStatus.PUBLISHED]) == 0

    def test_failed_can_retry(self):
        assert PostStatus.QUEUED in VALID_TRANSITIONS[PostStatus.FAILED]

    def test_failed_can_go_back_to_draft(self):
        assert PostStatus.DRAFT in VALID_TRANSITIONS[PostStatus.FAILED]


class TestPostModel:
    def test_can_transition_to_valid(self):
        import uuid
        post = Post(tenant_id=uuid.uuid4(), channel_id=uuid.uuid4(), platform="instagram")
        assert post.can_transition_to(PostStatus.QUEUED)

    def test_cannot_transition_to_invalid(self):
        import uuid
        post = Post(tenant_id=uuid.uuid4(), channel_id=uuid.uuid4(), platform="instagram")
        assert not post.can_transition_to(PostStatus.PUBLISHED)

    def test_failed_can_transition_to_queued(self):
        import uuid
        post = Post(
            tenant_id=uuid.uuid4(),
            channel_id=uuid.uuid4(),
            platform="instagram",
            status=PostStatus.FAILED,
        )
        assert post.can_transition_to(PostStatus.QUEUED)

    def test_default_status_is_draft(self):
        import uuid
        post = Post(tenant_id=uuid.uuid4(), channel_id=uuid.uuid4(), platform="instagram")
        assert post.status == PostStatus.DRAFT

    def test_new_fields_have_defaults(self):
        import uuid
        post = Post(tenant_id=uuid.uuid4(), channel_id=uuid.uuid4(), platform="instagram")
        assert post.retry_count == 0
        assert post.max_retries == 3
        assert post.last_error is None
        assert post.permalink is None
        assert post.policy_decision is None


class TestAllStatesHaveTransitions:
    """Every PostStatus value must appear as a key in VALID_TRANSITIONS."""

    def test_all_states_defined(self):
        for status in PostStatus:
            assert status in VALID_TRANSITIONS, f"{status} missing from VALID_TRANSITIONS"

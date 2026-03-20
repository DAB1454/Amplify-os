"""Worker-safe learning event capture helpers.

These helpers build structured event dicts that can be:
1. Passed directly to LearningEventService.emit() in API routes
2. Accumulated in a list and flushed via ingest_batch() in workers
3. Serialized to JSON for async queue-based ingestion

All helpers are pure functions — no DB access, no side effects.
They produce event dicts safe to replay at any time.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _idem(action_type: str, entity_id: str | uuid.UUID | None, ts: datetime) -> str:
    return f"{action_type}:{entity_id or 'none'}:{ts.isoformat()}"


def post_created(
    *,
    post_id: uuid.UUID,
    tenant_id: uuid.UUID,
    platform: str,
    campaign_id: uuid.UUID | None = None,
    artist_id: uuid.UUID | None = None,
    content_type: str | None = None,
    content_length: int | None = None,
    has_media: bool = False,
    source: str = "api",
) -> dict[str, Any]:
    """Event: a new post was created."""
    now = _now()
    return {
        "action_type": "post.created",
        "tenant_id": str(tenant_id),
        "post_id": str(post_id),
        "artist_id": str(artist_id) if artist_id else None,
        "campaign_id": str(campaign_id) if campaign_id else None,
        "platform": platform,
        "observed_at": now,
        "source": source,
        "idempotency_key": _idem("post.created", post_id, now),
        "features": {
            "content_type": content_type,
            "content_length": content_length,
            "has_media": has_media,
        },
    }


def post_queued(
    *,
    post_id: uuid.UUID,
    tenant_id: uuid.UUID,
    platform: str,
    policy_decision: str,
    policy_reasons: list[str] | None = None,
    campaign_id: uuid.UUID | None = None,
    artist_id: uuid.UUID | None = None,
    source: str = "api",
) -> dict[str, Any]:
    """Event: post submitted to approval queue (policy engine ran)."""
    now = _now()
    return {
        "action_type": "post.queued",
        "tenant_id": str(tenant_id),
        "post_id": str(post_id),
        "artist_id": str(artist_id) if artist_id else None,
        "campaign_id": str(campaign_id) if campaign_id else None,
        "platform": platform,
        "observed_at": now,
        "source": source,
        "idempotency_key": _idem("post.queued", post_id, now),
        "features": {
            "policy_decision": policy_decision,
            "policy_reasons": policy_reasons or [],
        },
    }


def policy_evaluated(
    *,
    post_id: uuid.UUID,
    tenant_id: uuid.UUID,
    platform: str,
    decision: str,
    verdicts: list[dict] | None = None,
    blocking_reasons: list[str] | None = None,
    approval_reasons: list[str] | None = None,
    source: str = "policy_engine",
) -> dict[str, Any]:
    """Event: policy engine evaluated a publish action."""
    now = _now()
    return {
        "action_type": "policy.evaluated",
        "tenant_id": str(tenant_id),
        "post_id": str(post_id),
        "platform": platform,
        "observed_at": now,
        "source": source,
        "idempotency_key": _idem("policy.evaluated", post_id, now),
        "features": {
            "decision": decision,
            "verdicts": verdicts or [],
            "blocking_reasons": blocking_reasons or [],
            "approval_reasons": approval_reasons or [],
        },
    }


def post_approved(
    *,
    post_id: uuid.UUID,
    tenant_id: uuid.UUID,
    platform: str | None = None,
    reviewer_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
    artist_id: uuid.UUID | None = None,
    source: str = "api",
) -> dict[str, Any]:
    """Event: post was approved."""
    now = _now()
    return {
        "action_type": "post.approved",
        "tenant_id": str(tenant_id),
        "post_id": str(post_id),
        "artist_id": str(artist_id) if artist_id else None,
        "campaign_id": str(campaign_id) if campaign_id else None,
        "platform": platform,
        "observed_at": now,
        "source": source,
        "idempotency_key": _idem("post.approved", post_id, now),
        "features": {
            "reviewer_id": str(reviewer_id) if reviewer_id else None,
        },
    }


def post_rejected(
    *,
    post_id: uuid.UUID,
    tenant_id: uuid.UUID,
    platform: str | None = None,
    reviewer_id: uuid.UUID | None = None,
    reason: str = "",
    source: str = "api",
) -> dict[str, Any]:
    """Event: post was rejected."""
    now = _now()
    return {
        "action_type": "post.rejected",
        "tenant_id": str(tenant_id),
        "post_id": str(post_id),
        "platform": platform,
        "observed_at": now,
        "source": source,
        "idempotency_key": _idem("post.rejected", post_id, now),
        "features": {
            "reviewer_id": str(reviewer_id) if reviewer_id else None,
            "reason": reason,
        },
    }


def post_scheduled(
    *,
    post_id: uuid.UUID,
    tenant_id: uuid.UUID,
    platform: str,
    scheduled_at: datetime,
    campaign_id: uuid.UUID | None = None,
    artist_id: uuid.UUID | None = None,
    source: str = "api",
) -> dict[str, Any]:
    """Event: post was scheduled for future publication."""
    now = _now()
    return {
        "action_type": "post.scheduled",
        "tenant_id": str(tenant_id),
        "post_id": str(post_id),
        "artist_id": str(artist_id) if artist_id else None,
        "campaign_id": str(campaign_id) if campaign_id else None,
        "platform": platform,
        "observed_at": now,
        "source": source,
        "idempotency_key": _idem("post.scheduled", post_id, now),
        "features": {
            "scheduled_at": scheduled_at.isoformat(),
        },
    }


def post_published(
    *,
    post_id: uuid.UUID,
    tenant_id: uuid.UUID,
    platform: str,
    platform_post_id: str | None = None,
    permalink: str | None = None,
    published_at: datetime | None = None,
    campaign_id: uuid.UUID | None = None,
    artist_id: uuid.UUID | None = None,
    source: str = "worker",
) -> dict[str, Any]:
    """Event: post was successfully published to a platform."""
    now = published_at or _now()
    return {
        "action_type": "post.published",
        "tenant_id": str(tenant_id),
        "post_id": str(post_id),
        "artist_id": str(artist_id) if artist_id else None,
        "campaign_id": str(campaign_id) if campaign_id else None,
        "platform": platform,
        "observed_at": now,
        "source": source,
        "idempotency_key": _idem("post.published", post_id, now),
        "features": {
            "platform_post_id": platform_post_id,
            "permalink": permalink,
        },
    }


def post_failed(
    *,
    post_id: uuid.UUID,
    tenant_id: uuid.UUID,
    platform: str,
    error: str,
    retry_count: int = 0,
    max_retries: int = 3,
    is_permanent: bool = False,
    campaign_id: uuid.UUID | None = None,
    artist_id: uuid.UUID | None = None,
    source: str = "worker",
) -> dict[str, Any]:
    """Event: post publish attempt failed."""
    now = _now()
    action = "post.failed_permanent" if is_permanent else "post.failed"
    return {
        "action_type": action,
        "tenant_id": str(tenant_id),
        "post_id": str(post_id),
        "artist_id": str(artist_id) if artist_id else None,
        "campaign_id": str(campaign_id) if campaign_id else None,
        "platform": platform,
        "observed_at": now,
        "source": source,
        "idempotency_key": _idem(action, post_id, now),
        "features": {
            "error": error[:500],
            "retry_count": retry_count,
            "max_retries": max_retries,
            "is_permanent": is_permanent,
        },
    }


def metrics_synced(
    *,
    post_id: uuid.UUID,
    tenant_id: uuid.UUID,
    platform: str,
    measurement_window: str = "24h",
    metrics: dict | None = None,
    source: str = "worker",
) -> dict[str, Any]:
    """Event: engagement metrics were collected for a post."""
    now = _now()
    return {
        "action_type": "metrics.synced",
        "tenant_id": str(tenant_id),
        "post_id": str(post_id),
        "platform": platform,
        "observed_at": now,
        "outcome_measured_at": now,
        "source": source,
        "idempotency_key": _idem(
            f"metrics.synced:{measurement_window}", post_id, now,
        ),
        "outcomes": metrics or {},
        "features": {
            "measurement_window": measurement_window,
        },
    }


def agent_decision(
    *,
    tenant_id: uuid.UUID,
    decision_type: str,
    post_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
    artist_id: uuid.UUID | None = None,
    platform: str | None = None,
    decision: str,
    reasoning: str = "",
    alternatives_considered: list[dict] | None = None,
    prompt_version_id: uuid.UUID | None = None,
    source: str = "agent",
) -> dict[str, Any]:
    """Event: an agent made a content/scheduling/strategy decision."""
    now = _now()
    entity = post_id or campaign_id
    return {
        "action_type": f"agent.{decision_type}",
        "tenant_id": str(tenant_id),
        "post_id": str(post_id) if post_id else None,
        "artist_id": str(artist_id) if artist_id else None,
        "campaign_id": str(campaign_id) if campaign_id else None,
        "platform": platform,
        "observed_at": now,
        "source": source,
        "idempotency_key": _idem(f"agent.{decision_type}", entity, now),
        "features": {
            "decision": decision,
            "reasoning": reasoning,
            "alternatives_considered": alternatives_considered or [],
            "prompt_version_id": str(prompt_version_id) if prompt_version_id else None,
        },
    }

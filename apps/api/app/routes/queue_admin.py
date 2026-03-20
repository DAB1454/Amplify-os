"""Queue health and admin routes.

Exposes job queue metrics, DLQ inspection, and replay for operators.
All routes require admin role.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.middleware.rbac import require_role

router = APIRouter(
    prefix="/admin/queue",
    tags=["admin", "queue"],
    dependencies=[Depends(require_role("admin"))],
)


def _get_redis(request: Request):
    r = getattr(request.app.state, "redis", None)
    if r is None:
        raise HTTPException(status_code=503, detail="Redis not available")
    return r


@router.get("/health")
async def queue_health(request: Request):
    """Return queue health snapshot: pending, processing, DLQ size, metrics."""
    from worker.app.queue import JobQueue

    r = _get_redis(request)
    q = JobQueue(r)
    health = await q.get_health()
    return asdict(health)


@router.get("/job/{job_id}")
async def get_job_status(job_id: str, request: Request):
    """Return the current state of a specific job."""
    from worker.app.queue import JobQueue

    r = _get_redis(request)
    q = JobQueue(r)
    state = await q.get_status(job_id)
    return {
        "job_id": state.job_id,
        "status": state.status,
        "job_type": state.job_type,
        "attempt": state.attempt,
        "max_retries": state.max_retries,
        "enqueued_at": state.enqueued_at,
        "started_at": state.started_at,
        "completed_at": state.completed_at,
        "last_error": state.last_error,
        "result": state.result,
    }


@router.get("/dlq")
async def peek_dlq(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
):
    """Peek at the first N entries in the dead-letter queue."""
    from worker.app.queue import JobQueue

    r = _get_redis(request)
    q = JobQueue(r)
    return await q.peek_dlq(limit=limit)


@router.post("/dlq/{index}/replay")
async def replay_dlq_entry(index: int, request: Request):
    """Move a DLQ entry back to the main queue for retry."""
    from worker.app.queue import JobQueue

    r = _get_redis(request)
    q = JobQueue(r)
    job_id = await q.replay_dlq_job(index=index)
    if job_id is None:
        raise HTTPException(status_code=404, detail="DLQ entry not found at index")
    return {"replayed_job_id": job_id}


@router.delete("/dlq")
async def flush_dlq(request: Request):
    """Clear the entire dead-letter queue."""
    from worker.app.queue import JobQueue

    r = _get_redis(request)
    q = JobQueue(r)
    count = await q.flush_dlq()
    return {"removed": count}


@router.post("/recover-stuck")
async def trigger_stuck_recovery(
    request: Request,
    max_age_seconds: int = Query(default=300, ge=10),
):
    """Manually trigger stuck-job recovery."""
    from worker.app.queue import JobQueue

    r = _get_redis(request)
    q = JobQueue(r)
    recovered = await q.recover_stuck_jobs(max_age_seconds=max_age_seconds)
    return {"recovered": recovered}

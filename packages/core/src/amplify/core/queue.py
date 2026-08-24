"""Production-grade Redis job queue with retries, DLQ, deduplication, and stuck-job recovery.

Redis key layout:
    amplify:queue              — main work queue (LIST, RPUSH/BLPOP)
    amplify:processing         — jobs currently being executed (HASH: job_id → job JSON)
    amplify:job:{job_id}       — per-job state (HASH: status, attempts, result, ...)
    amplify:dlq                — dead-letter queue (LIST of failed job JSON)
    amplify:dedup:{idempotency_key} — deduplication guard (STRING with TTL)

Job JSON envelope:
{
    "job_id":           "hex-uuid",
    "job_type":         "publish_post",
    "payload":          { ... },
    "idempotency_key":  "optional-caller-supplied-key",
    "enqueued_at":      "ISO-8601",
    "max_retries":      3,
    "attempt":          0,
    "backoff_base":     2,
    "timeout_seconds":  300
}
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import redis.asyncio as redis

logger = logging.getLogger(__name__)

# ── Redis key constants ─────────────────────────────────────────────────

QUEUE_KEY = "amplify:queue"
PROCESSING_KEY = "amplify:processing"
JOB_PREFIX = "amplify:job:"
DLQ_KEY = "amplify:dlq"
DEDUP_PREFIX = "amplify:dedup:"
METRICS_KEY = "amplify:queue_metrics"

# ── Defaults ────────────────────────────────────────────────────────────

DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 2  # seconds
DEFAULT_BACKOFF_CAP = 300  # 5 minutes
DEFAULT_JOB_TIMEOUT = 300  # 5 minutes
DEFAULT_DEDUP_TTL = 3600  # 1 hour
DEFAULT_JOB_STATE_TTL = 86400 * 7  # 7 days


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    RETRYING = "retrying"
    DEAD = "dead"


@dataclass
class JobEnvelope:
    """Full job envelope as stored in Redis."""

    job_id: str
    job_type: str
    payload: dict[str, Any]
    idempotency_key: str = ""
    enqueued_at: str = ""
    max_retries: int = DEFAULT_MAX_RETRIES
    attempt: int = 0
    backoff_base: int = DEFAULT_BACKOFF_BASE
    timeout_seconds: int = DEFAULT_JOB_TIMEOUT

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> JobEnvelope:
        data = json.loads(raw)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class JobState:
    """Queryable state for a single job."""

    job_id: str
    status: str
    job_type: str = ""
    attempt: int = 0
    max_retries: int = DEFAULT_MAX_RETRIES
    enqueued_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    last_error: str = ""
    result: str = ""


@dataclass
class QueueHealth:
    """Snapshot of queue health for admin inspection."""

    pending: int = 0
    processing: int = 0
    dead_letter: int = 0
    completed_24h: int = 0
    failed_24h: int = 0
    oldest_processing_age_seconds: float = 0.0


def backoff_delay(attempt: int, base: int = DEFAULT_BACKOFF_BASE, cap: int = DEFAULT_BACKOFF_CAP) -> float:
    """Exponential backoff with jitter: base * 2^attempt, capped."""
    delay = min(base * (2 ** attempt), cap)
    return delay


class JobQueue:
    """Production Redis job queue with retries, DLQ, dedup, and recovery."""

    def __init__(self, redis_client: redis.Redis) -> None:
        self._redis = redis_client

    # ── Enqueue ─────────────────────────────────────────────────────

    async def enqueue(
        self,
        job_type: str,
        payload: dict,
        *,
        idempotency_key: str = "",
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: int = DEFAULT_BACKOFF_BASE,
        timeout_seconds: int = DEFAULT_JOB_TIMEOUT,
    ) -> str | None:
        """Push a job onto the queue.

        Returns the job_id, or None if deduplicated (already processed/in-flight).
        """
        # ── Dedup check ─────────────────────────────────────────────
        if idempotency_key:
            dedup_key = f"{DEDUP_PREFIX}{idempotency_key}"
            was_set = await self._redis.set(dedup_key, "1", nx=True, ex=DEFAULT_DEDUP_TTL)
            if not was_set:
                logger.info("Deduplicated job with key=%s", idempotency_key)
                return None

        job_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()

        envelope = JobEnvelope(
            job_id=job_id,
            job_type=job_type,
            payload=payload,
            idempotency_key=idempotency_key,
            enqueued_at=now,
            max_retries=max_retries,
            attempt=0,
            backoff_base=backoff_base,
            timeout_seconds=timeout_seconds,
        )

        pipe = self._redis.pipeline()
        pipe.rpush(QUEUE_KEY, envelope.to_json())
        pipe.hset(f"{JOB_PREFIX}{job_id}", mapping={
            "status": JobStatus.QUEUED.value,
            "job_type": job_type,
            "attempt": "0",
            "max_retries": str(max_retries),
            "enqueued_at": now,
            "started_at": "",
            "completed_at": "",
            "last_error": "",
            "result": "",
        })
        pipe.expire(f"{JOB_PREFIX}{job_id}", DEFAULT_JOB_STATE_TTL)
        await pipe.execute()

        logger.info("Enqueued job_id=%s type=%s", job_id, job_type)
        return job_id

    # ── Dequeue ─────────────────────────────────────────────────────

    async def dequeue(self, timeout: int = 2) -> JobEnvelope | None:
        """BLPOP a job, move it to the processing set. Returns envelope or None."""
        result = await self._redis.blpop(QUEUE_KEY, timeout=timeout)
        if result is None:
            return None

        _key, raw = result
        try:
            envelope = JobEnvelope.from_json(raw)
        except (json.JSONDecodeError, TypeError) as e:
            logger.error("Corrupt job payload, discarding: %s", e)
            return None

        now = datetime.now(timezone.utc).isoformat()

        pipe = self._redis.pipeline()
        # Track in processing set with timestamp for stuck-job detection
        pipe.hset(PROCESSING_KEY, envelope.job_id, json.dumps({
            "envelope": raw if isinstance(raw, str) else raw.decode(),
            "started_at": now,
        }))
        pipe.hset(f"{JOB_PREFIX}{envelope.job_id}", mapping={
            "status": JobStatus.PROCESSING.value,
            "started_at": now,
            "attempt": str(envelope.attempt),
        })
        await pipe.execute()

        return envelope

    # ── Ack (success) ───────────────────────────────────────────────

    async def ack(self, job_id: str, result: dict | None = None) -> None:
        """Mark a job as completed and remove from processing set."""
        now = datetime.now(timezone.utc).isoformat()
        result_str = json.dumps(result) if result else ""

        pipe = self._redis.pipeline()
        pipe.hdel(PROCESSING_KEY, job_id)
        pipe.hset(f"{JOB_PREFIX}{job_id}", mapping={
            "status": JobStatus.COMPLETED.value,
            "completed_at": now,
            "result": result_str,
        })
        pipe.hincrby(METRICS_KEY, "completed_total", 1)
        await pipe.execute()

        logger.info("Acked job_id=%s", job_id)

    # ── Nack (failure) ──────────────────────────────────────────────

    async def nack(self, envelope: JobEnvelope, error: str) -> None:
        """Handle a failed job: retry with backoff or send to DLQ."""
        envelope.attempt += 1
        now = datetime.now(timezone.utc).isoformat()

        if envelope.attempt >= envelope.max_retries:
            await self._send_to_dlq(envelope, error)
            return

        # Schedule retry by re-enqueueing after a delay
        delay = backoff_delay(envelope.attempt, envelope.backoff_base)
        logger.warning(
            "Job %s failed (attempt %d/%d), retrying in %.0fs: %s",
            envelope.job_id, envelope.attempt, envelope.max_retries, delay, error,
        )

        pipe = self._redis.pipeline()
        pipe.hdel(PROCESSING_KEY, envelope.job_id)
        pipe.hset(f"{JOB_PREFIX}{envelope.job_id}", mapping={
            "status": JobStatus.RETRYING.value,
            "attempt": str(envelope.attempt),
            "last_error": error[:1000],
        })
        pipe.hincrby(METRICS_KEY, "retries_total", 1)
        await pipe.execute()

        # Re-enqueue after delay (in the worker loop, we use asyncio.sleep)
        await asyncio.sleep(delay)
        await self._redis.rpush(QUEUE_KEY, envelope.to_json())

    async def _send_to_dlq(self, envelope: JobEnvelope, error: str) -> None:
        """Move a permanently failed job to the dead-letter queue."""
        now = datetime.now(timezone.utc).isoformat()
        dlq_entry = json.dumps({
            "envelope": asdict(envelope),
            "final_error": error[:2000],
            "dead_at": now,
        })

        pipe = self._redis.pipeline()
        pipe.hdel(PROCESSING_KEY, envelope.job_id)
        pipe.rpush(DLQ_KEY, dlq_entry)
        pipe.hset(f"{JOB_PREFIX}{envelope.job_id}", mapping={
            "status": JobStatus.DEAD.value,
            "last_error": error[:1000],
            "completed_at": now,
        })
        pipe.hincrby(METRICS_KEY, "dead_total", 1)
        await pipe.execute()

        logger.error(
            "Job %s moved to DLQ after %d attempts: %s",
            envelope.job_id, envelope.attempt, error,
        )

    # ── Stuck job recovery ──────────────────────────────────────────

    async def recover_stuck_jobs(self, max_age_seconds: int = DEFAULT_JOB_TIMEOUT) -> int:
        """Re-enqueue jobs stuck in processing longer than max_age_seconds.

        Returns the number of recovered jobs.
        """
        now = time.time()
        processing = await self._redis.hgetall(PROCESSING_KEY)
        recovered = 0

        for job_id, raw_info in processing.items():
            try:
                info = json.loads(raw_info)
                started_at = datetime.fromisoformat(info["started_at"])
                age = now - started_at.timestamp()
            except (json.JSONDecodeError, KeyError, ValueError):
                age = max_age_seconds + 1  # Force recovery of corrupt entries

            if age > max_age_seconds:
                try:
                    envelope_raw = info.get("envelope", "{}")
                    envelope = JobEnvelope.from_json(envelope_raw)
                    envelope.attempt += 1

                    if envelope.attempt >= envelope.max_retries:
                        await self._send_to_dlq(envelope, "stuck job recovered — max retries exceeded")
                    else:
                        await self._redis.rpush(QUEUE_KEY, envelope.to_json())
                        await self._redis.hdel(PROCESSING_KEY, job_id)
                        await self._redis.hset(f"{JOB_PREFIX}{job_id}", mapping={
                            "status": JobStatus.RETRYING.value,
                            "attempt": str(envelope.attempt),
                            "last_error": "recovered from stuck state",
                        })

                    recovered += 1
                    logger.warning("Recovered stuck job %s (age=%.0fs)", job_id, age)
                except Exception:
                    logger.exception("Failed to recover stuck job %s", job_id)
                    await self._redis.hdel(PROCESSING_KEY, job_id)
                    recovered += 1

        if recovered:
            await self._redis.hincrby(METRICS_KEY, "recovered_total", recovered)

        return recovered

    # ── Query / admin ───────────────────────────────────────────────

    async def get_status(self, job_id: str) -> JobState:
        """Return the current state of a job."""
        data = await self._redis.hgetall(f"{JOB_PREFIX}{job_id}")
        if not data:
            return JobState(job_id=job_id, status="unknown")
        return JobState(
            job_id=job_id,
            status=data.get("status", "unknown"),
            job_type=data.get("job_type", ""),
            attempt=int(data.get("attempt", 0)),
            max_retries=int(data.get("max_retries", DEFAULT_MAX_RETRIES)),
            enqueued_at=data.get("enqueued_at", ""),
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at", ""),
            last_error=data.get("last_error", ""),
            result=data.get("result", ""),
        )

    async def get_health(self) -> QueueHealth:
        """Return a snapshot of queue health metrics."""
        pipe = self._redis.pipeline()
        pipe.llen(QUEUE_KEY)
        pipe.hlen(PROCESSING_KEY)
        pipe.llen(DLQ_KEY)
        pipe.hgetall(METRICS_KEY)
        results = await pipe.execute()

        pending, processing, dlq_size, raw_metrics = results

        # Find oldest processing job for staleness check
        oldest_age = 0.0
        if processing > 0:
            all_processing = await self._redis.hgetall(PROCESSING_KEY)
            now = time.time()
            for _jid, raw_info in all_processing.items():
                try:
                    info = json.loads(raw_info)
                    started = datetime.fromisoformat(info["started_at"]).timestamp()
                    age = now - started
                    oldest_age = max(oldest_age, age)
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass

        return QueueHealth(
            pending=pending,
            processing=processing,
            dead_letter=dlq_size,
            completed_24h=int(raw_metrics.get("completed_total", 0)),
            failed_24h=int(raw_metrics.get("dead_total", 0)),
            oldest_processing_age_seconds=round(oldest_age, 1),
        )

    async def peek_dlq(self, limit: int = 20) -> list[dict]:
        """Return the first N entries from the dead-letter queue."""
        raw_entries = await self._redis.lrange(DLQ_KEY, 0, limit - 1)
        entries = []
        for raw in raw_entries:
            try:
                entries.append(json.loads(raw))
            except json.JSONDecodeError:
                entries.append({"raw": str(raw)[:500]})
        return entries

    async def replay_dlq_job(self, index: int = 0) -> str | None:
        """Move a job from the DLQ back to the main queue for retry.

        Returns the job_id or None if the index is out of range.
        """
        raw = await self._redis.lindex(DLQ_KEY, index)
        if raw is None:
            return None

        try:
            entry = json.loads(raw)
            envelope_data = entry.get("envelope", {})
            envelope = JobEnvelope(**{
                k: v for k, v in envelope_data.items()
                if k in JobEnvelope.__dataclass_fields__
            })
            envelope.attempt = 0  # Reset attempts for replay
            await self._redis.rpush(QUEUE_KEY, envelope.to_json())
            await self._redis.lrem(DLQ_KEY, 1, raw)
            logger.info("Replayed DLQ job %s", envelope.job_id)
            return envelope.job_id
        except Exception:
            logger.exception("Failed to replay DLQ entry at index %d", index)
            return None

    async def flush_dlq(self) -> int:
        """Clear the dead-letter queue. Returns count removed."""
        count = await self._redis.llen(DLQ_KEY)
        if count > 0:
            await self._redis.delete(DLQ_KEY)
        return count

    async def queue_size(self) -> int:
        """Return the number of jobs waiting in the main queue."""
        return await self._redis.llen(QUEUE_KEY)


# Need asyncio for the nack retry delay
import asyncio  # noqa: E402

"""Tests for the production Redis job queue.

Covers: enqueue/dequeue, deduplication, retry with backoff, DLQ,
stuck-job recovery, poison/corrupt payloads, and admin operations.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.queue import (
    DEDUP_PREFIX,
    DLQ_KEY,
    JOB_PREFIX,
    METRICS_KEY,
    PROCESSING_KEY,
    QUEUE_KEY,
    JobEnvelope,
    JobQueue,
    JobStatus,
    QueueHealth,
    backoff_delay,
)

pytestmark = pytest.mark.asyncio


# ── Helpers ────────────────────────────────────────────────────────────


class FakeRedis:
    """Minimal in-memory Redis mock for queue tests.

    Supports the exact Redis commands the JobQueue uses: LIST ops (rpush,
    blpop, llen, lrange, lindex, lrem), HASH ops (hset, hget, hgetall,
    hdel, hlen, hincrby), STRING ops (set with NX/EX), and delete.
    """

    def __init__(self) -> None:
        self._data: dict[str, list | dict | str] = {}

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    # ── LIST ──────────────────────────────────────────────────

    async def rpush(self, key: str, value: str) -> int:
        lst = self._data.setdefault(key, [])
        assert isinstance(lst, list)
        lst.append(value)
        return len(lst)

    async def blpop(self, key: str, timeout: int = 0) -> tuple[str, str] | None:
        lst = self._data.get(key)
        if not lst or not isinstance(lst, list) or len(lst) == 0:
            return None
        return (key, lst.pop(0))

    async def llen(self, key: str) -> int:
        lst = self._data.get(key)
        if not lst or not isinstance(lst, list):
            return 0
        return len(lst)

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        lst = self._data.get(key)
        if not lst or not isinstance(lst, list):
            return []
        return lst[start:stop + 1]

    async def lindex(self, key: str, index: int) -> str | None:
        lst = self._data.get(key)
        if not lst or not isinstance(lst, list) or index >= len(lst):
            return None
        return lst[index]

    async def lrem(self, key: str, count: int, value: str) -> int:
        lst = self._data.get(key)
        if not lst or not isinstance(lst, list):
            return 0
        removed = 0
        to_remove = abs(count) if count != 0 else len(lst)
        new_list = []
        for item in lst:
            if item == value and removed < to_remove:
                removed += 1
            else:
                new_list.append(item)
        self._data[key] = new_list
        return removed

    # ── HASH ──────────────────────────────────────────────────

    async def hset(self, key: str, *args, mapping: dict | None = None, **kwargs) -> int:
        h = self._data.setdefault(key, {})
        assert isinstance(h, dict)
        if mapping:
            h.update({str(k): str(v) for k, v in mapping.items()})
        # Handle hset(key, field, value)
        if len(args) == 2:
            h[str(args[0])] = str(args[1])
        return 1

    async def hget(self, key: str, field: str) -> str | None:
        h = self._data.get(key)
        if not h or not isinstance(h, dict):
            return None
        return h.get(str(field))

    async def hgetall(self, key: str) -> dict[str, str]:
        h = self._data.get(key)
        if not h or not isinstance(h, dict):
            return {}
        return dict(h)

    async def hdel(self, key: str, *fields: str) -> int:
        h = self._data.get(key)
        if not h or not isinstance(h, dict):
            return 0
        removed = 0
        for f in fields:
            if str(f) in h:
                del h[str(f)]
                removed += 1
        return removed

    async def hlen(self, key: str) -> int:
        h = self._data.get(key)
        if not h or not isinstance(h, dict):
            return 0
        return len(h)

    async def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        h = self._data.setdefault(key, {})
        assert isinstance(h, dict)
        current = int(h.get(field, 0))
        h[field] = str(current + amount)
        return current + amount

    # ── STRING ────────────────────────────────────────────────

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and key in self._data:
            return None  # Key exists, NX fails
        self._data[key] = value
        return True

    async def get(self, key: str) -> str | None:
        val = self._data.get(key)
        if isinstance(val, str):
            return val
        return None

    # ── KEY ───────────────────────────────────────────────────

    async def delete(self, *keys: str) -> int:
        removed = 0
        for k in keys:
            if k in self._data:
                del self._data[k]
                removed += 1
        return removed

    async def expire(self, key: str, seconds: int) -> bool:
        return key in self._data

    async def aclose(self) -> None:
        pass


class FakePipeline:
    """Batches commands and executes them sequentially."""

    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._commands: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def method(*args, **kwargs):
            self._commands.append((name, args, kwargs))
            return self  # allow chaining
        return method

    async def execute(self) -> list:
        results = []
        for name, args, kwargs in self._commands:
            fn = getattr(self._redis, name)
            result = await fn(*args, **kwargs)
            results.append(result)
        self._commands.clear()
        return results


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def queue(fake_redis: FakeRedis) -> JobQueue:
    return JobQueue(fake_redis)


# ── Unit: backoff_delay ────────────────────────────────────────────────


class TestBackoffDelay:
    def test_first_attempt(self):
        assert backoff_delay(0) == 2  # base * 2^0 = 2

    def test_exponential_growth(self):
        assert backoff_delay(1) == 4   # 2 * 2^1
        assert backoff_delay(2) == 8   # 2 * 2^2
        assert backoff_delay(3) == 16  # 2 * 2^3

    def test_cap(self):
        assert backoff_delay(20, base=2, cap=300) == 300

    def test_custom_base(self):
        assert backoff_delay(0, base=5) == 5
        assert backoff_delay(1, base=5) == 10


# ── JobEnvelope serialization ──────────────────────────────────────────


class TestJobEnvelope:
    def test_round_trip(self):
        env = JobEnvelope(
            job_id="abc123",
            job_type="publish_post",
            payload={"post_id": "p1"},
            idempotency_key="key1",
            max_retries=5,
        )
        raw = env.to_json()
        restored = JobEnvelope.from_json(raw)
        assert restored.job_id == "abc123"
        assert restored.job_type == "publish_post"
        assert restored.payload == {"post_id": "p1"}
        assert restored.idempotency_key == "key1"
        assert restored.max_retries == 5

    def test_from_json_ignores_extra_fields(self):
        data = {
            "job_id": "x",
            "job_type": "test",
            "payload": {},
            "unknown_field": "ignored",
        }
        env = JobEnvelope.from_json(json.dumps(data))
        assert env.job_id == "x"
        assert not hasattr(env, "unknown_field")

    def test_from_json_invalid_raises(self):
        with pytest.raises(json.JSONDecodeError):
            JobEnvelope.from_json("not json")


# ── Enqueue ────────────────────────────────────────────────────────────


class TestEnqueue:
    async def test_enqueue_returns_job_id(self, queue: JobQueue):
        job_id = await queue.enqueue("publish_post", {"post_id": "p1"})
        assert job_id is not None
        assert len(job_id) == 32  # hex uuid

    async def test_enqueue_sets_job_state(self, queue: JobQueue, fake_redis: FakeRedis):
        job_id = await queue.enqueue("publish_post", {"post_id": "p1"})
        state = await fake_redis.hgetall(f"{JOB_PREFIX}{job_id}")
        assert state["status"] == "queued"
        assert state["job_type"] == "publish_post"

    async def test_enqueue_pushes_to_queue(self, queue: JobQueue, fake_redis: FakeRedis):
        await queue.enqueue("test_job", {"key": "val"})
        size = await fake_redis.llen(QUEUE_KEY)
        assert size == 1

    async def test_multiple_enqueues(self, queue: JobQueue, fake_redis: FakeRedis):
        await queue.enqueue("job_a", {})
        await queue.enqueue("job_b", {})
        await queue.enqueue("job_c", {})
        assert await fake_redis.llen(QUEUE_KEY) == 3


# ── Deduplication ──────────────────────────────────────────────────────


class TestDeduplication:
    async def test_duplicate_idempotency_key_returns_none(self, queue: JobQueue):
        job_id_1 = await queue.enqueue("test", {}, idempotency_key="unique-key")
        job_id_2 = await queue.enqueue("test", {}, idempotency_key="unique-key")
        assert job_id_1 is not None
        assert job_id_2 is None

    async def test_different_keys_both_enqueue(self, queue: JobQueue, fake_redis: FakeRedis):
        await queue.enqueue("test", {}, idempotency_key="key-a")
        await queue.enqueue("test", {}, idempotency_key="key-b")
        assert await fake_redis.llen(QUEUE_KEY) == 2

    async def test_no_key_always_enqueues(self, queue: JobQueue, fake_redis: FakeRedis):
        await queue.enqueue("test", {})
        await queue.enqueue("test", {})
        assert await fake_redis.llen(QUEUE_KEY) == 2


# ── Dequeue ────────────────────────────────────────────────────────────


class TestDequeue:
    async def test_dequeue_returns_envelope(self, queue: JobQueue):
        await queue.enqueue("publish_post", {"post_id": "p1"})
        envelope = await queue.dequeue(timeout=1)
        assert envelope is not None
        assert envelope.job_type == "publish_post"
        assert envelope.payload == {"post_id": "p1"}

    async def test_dequeue_empty_returns_none(self, queue: JobQueue):
        envelope = await queue.dequeue(timeout=0)
        assert envelope is None

    async def test_dequeue_moves_to_processing(self, queue: JobQueue, fake_redis: FakeRedis):
        job_id = await queue.enqueue("test", {})
        envelope = await queue.dequeue(timeout=1)
        assert envelope is not None
        processing = await fake_redis.hgetall(PROCESSING_KEY)
        assert envelope.job_id in processing

    async def test_dequeue_sets_processing_status(self, queue: JobQueue, fake_redis: FakeRedis):
        job_id = await queue.enqueue("test", {})
        envelope = await queue.dequeue(timeout=1)
        state = await fake_redis.hgetall(f"{JOB_PREFIX}{envelope.job_id}")
        assert state["status"] == "processing"

    async def test_corrupt_payload_returns_none(self, queue: JobQueue, fake_redis: FakeRedis):
        """Poison message: invalid JSON is discarded, dequeue returns None."""
        await fake_redis.rpush(QUEUE_KEY, "not-valid-json{{{")
        envelope = await queue.dequeue(timeout=1)
        assert envelope is None


# ── Ack (success) ──────────────────────────────────────────────────────


class TestAck:
    async def test_ack_removes_from_processing(self, queue: JobQueue, fake_redis: FakeRedis):
        await queue.enqueue("test", {})
        envelope = await queue.dequeue(timeout=1)
        await queue.ack(envelope.job_id, {"ok": True})
        processing = await fake_redis.hgetall(PROCESSING_KEY)
        assert envelope.job_id not in processing

    async def test_ack_sets_completed_status(self, queue: JobQueue, fake_redis: FakeRedis):
        await queue.enqueue("test", {})
        envelope = await queue.dequeue(timeout=1)
        await queue.ack(envelope.job_id)
        state = await fake_redis.hgetall(f"{JOB_PREFIX}{envelope.job_id}")
        assert state["status"] == "completed"

    async def test_ack_increments_metrics(self, queue: JobQueue, fake_redis: FakeRedis):
        await queue.enqueue("test", {})
        envelope = await queue.dequeue(timeout=1)
        await queue.ack(envelope.job_id)
        metrics = await fake_redis.hgetall(METRICS_KEY)
        assert int(metrics.get("completed_total", 0)) == 1


# ── Nack (failure with retry) ─────────────────────────────────────────


class TestNack:
    async def test_nack_retries_when_under_max(self, queue: JobQueue, fake_redis: FakeRedis):
        """First failure should re-enqueue for retry."""
        await queue.enqueue("test", {}, max_retries=3)
        envelope = await queue.dequeue(timeout=1)

        with patch("app.queue.asyncio.sleep", new_callable=AsyncMock):
            await queue.nack(envelope, "connection timeout")

        # Should be back on the queue
        assert await fake_redis.llen(QUEUE_KEY) == 1
        state = await fake_redis.hgetall(f"{JOB_PREFIX}{envelope.job_id}")
        assert state["status"] == "retrying"
        assert state["attempt"] == "1"

    async def test_nack_sends_to_dlq_at_max_retries(self, queue: JobQueue, fake_redis: FakeRedis):
        """After max retries, job goes to DLQ."""
        await queue.enqueue("test", {}, max_retries=1)
        envelope = await queue.dequeue(timeout=1)

        await queue.nack(envelope, "permanent failure")

        # Should NOT be on the main queue
        assert await fake_redis.llen(QUEUE_KEY) == 0
        # Should be in DLQ
        assert await fake_redis.llen(DLQ_KEY) == 1
        state = await fake_redis.hgetall(f"{JOB_PREFIX}{envelope.job_id}")
        assert state["status"] == "dead"

    async def test_nack_increments_attempt(self, queue: JobQueue, fake_redis: FakeRedis):
        await queue.enqueue("test", {}, max_retries=5)
        envelope = await queue.dequeue(timeout=1)

        with patch("app.queue.asyncio.sleep", new_callable=AsyncMock):
            await queue.nack(envelope, "err1")

        # Re-dequeue the retried job
        retried = await queue.dequeue(timeout=1)
        assert retried is not None
        assert retried.attempt == 1

    async def test_nack_truncates_long_error(self, queue: JobQueue, fake_redis: FakeRedis):
        await queue.enqueue("test", {}, max_retries=3)
        envelope = await queue.dequeue(timeout=1)
        long_error = "x" * 5000

        with patch("app.queue.asyncio.sleep", new_callable=AsyncMock):
            await queue.nack(envelope, long_error)

        state = await fake_redis.hgetall(f"{JOB_PREFIX}{envelope.job_id}")
        assert len(state["last_error"]) <= 1000


# ── DLQ operations ────────────────────────────────────────────────────


class TestDLQ:
    async def _push_to_dlq(self, queue: JobQueue, fake_redis: FakeRedis, count: int = 1) -> list[str]:
        """Helper: enqueue and fail jobs until they hit DLQ."""
        job_ids = []
        for i in range(count):
            job_id = await queue.enqueue(f"test_{i}", {}, max_retries=1)
            envelope = await queue.dequeue(timeout=1)
            await queue.nack(envelope, f"error {i}")
            job_ids.append(job_id)
        return job_ids

    async def test_peek_dlq(self, queue: JobQueue, fake_redis: FakeRedis):
        await self._push_to_dlq(queue, fake_redis, count=3)
        entries = await queue.peek_dlq(limit=10)
        assert len(entries) == 3
        assert "final_error" in entries[0]

    async def test_replay_dlq_job(self, queue: JobQueue, fake_redis: FakeRedis):
        await self._push_to_dlq(queue, fake_redis, count=1)
        assert await fake_redis.llen(QUEUE_KEY) == 0

        job_id = await queue.replay_dlq_job(index=0)
        assert job_id is not None
        # Should be back on the main queue
        assert await fake_redis.llen(QUEUE_KEY) == 1
        # Should be removed from DLQ
        assert await fake_redis.llen(DLQ_KEY) == 0

    async def test_replay_dlq_resets_attempts(self, queue: JobQueue, fake_redis: FakeRedis):
        await self._push_to_dlq(queue, fake_redis, count=1)
        await queue.replay_dlq_job(index=0)

        envelope = await queue.dequeue(timeout=1)
        assert envelope is not None
        assert envelope.attempt == 0

    async def test_replay_dlq_invalid_index(self, queue: JobQueue):
        result = await queue.replay_dlq_job(index=99)
        assert result is None

    async def test_flush_dlq(self, queue: JobQueue, fake_redis: FakeRedis):
        await self._push_to_dlq(queue, fake_redis, count=5)
        assert await fake_redis.llen(DLQ_KEY) == 5

        count = await queue.flush_dlq()
        assert count == 5
        assert await fake_redis.llen(DLQ_KEY) == 0

    async def test_flush_empty_dlq(self, queue: JobQueue):
        count = await queue.flush_dlq()
        assert count == 0


# ── Stuck job recovery ─────────────────────────────────────────────────


class TestStuckJobRecovery:
    async def test_recovers_stuck_job(self, queue: JobQueue, fake_redis: FakeRedis):
        """A job stuck in processing beyond timeout should be re-enqueued."""
        job_id = await queue.enqueue("test", {}, max_retries=3)
        envelope = await queue.dequeue(timeout=1)

        # Manually backdate the processing entry
        old_time = "2020-01-01T00:00:00+00:00"
        processing_data = json.dumps({
            "envelope": envelope.to_json(),
            "started_at": old_time,
        })
        await fake_redis.hset(PROCESSING_KEY, envelope.job_id, processing_data)

        recovered = await queue.recover_stuck_jobs(max_age_seconds=60)
        assert recovered == 1
        # Should be back on the main queue
        assert await fake_redis.llen(QUEUE_KEY) == 1
        # Should be removed from processing
        processing = await fake_redis.hgetall(PROCESSING_KEY)
        assert envelope.job_id not in processing

    async def test_does_not_recover_fresh_job(self, queue: JobQueue, fake_redis: FakeRedis):
        """A job recently started should NOT be recovered."""
        await queue.enqueue("test", {})
        await queue.dequeue(timeout=1)

        recovered = await queue.recover_stuck_jobs(max_age_seconds=3600)
        assert recovered == 0

    async def test_stuck_job_at_max_retries_goes_to_dlq(self, queue: JobQueue, fake_redis: FakeRedis):
        """A stuck job that's exhausted retries goes to DLQ, not back to queue."""
        job_id = await queue.enqueue("test", {}, max_retries=1)
        envelope = await queue.dequeue(timeout=1)

        # Backdate
        old_time = "2020-01-01T00:00:00+00:00"
        processing_data = json.dumps({
            "envelope": envelope.to_json(),
            "started_at": old_time,
        })
        await fake_redis.hset(PROCESSING_KEY, envelope.job_id, processing_data)

        recovered = await queue.recover_stuck_jobs(max_age_seconds=60)
        assert recovered == 1
        assert await fake_redis.llen(QUEUE_KEY) == 0
        assert await fake_redis.llen(DLQ_KEY) == 1

    async def test_corrupt_processing_entry_is_cleaned(self, queue: JobQueue, fake_redis: FakeRedis):
        """Corrupt entries in processing should be cleaned up."""
        await fake_redis.hset(PROCESSING_KEY, "bad-job", "not-json")
        recovered = await queue.recover_stuck_jobs(max_age_seconds=60)
        assert recovered == 1
        processing = await fake_redis.hgetall(PROCESSING_KEY)
        assert "bad-job" not in processing


# ── Job status query ──────────────────────────────────────────────────


class TestJobStatus:
    async def test_queued_status(self, queue: JobQueue):
        job_id = await queue.enqueue("test", {})
        state = await queue.get_status(job_id)
        assert state.status == "queued"
        assert state.job_type == "test"

    async def test_processing_status(self, queue: JobQueue):
        job_id = await queue.enqueue("test", {})
        await queue.dequeue(timeout=1)
        state = await queue.get_status(job_id)
        assert state.status == "processing"

    async def test_completed_status(self, queue: JobQueue):
        job_id = await queue.enqueue("test", {})
        envelope = await queue.dequeue(timeout=1)
        await queue.ack(job_id)
        state = await queue.get_status(job_id)
        assert state.status == "completed"

    async def test_unknown_job(self, queue: JobQueue):
        state = await queue.get_status("nonexistent")
        assert state.status == "unknown"


# ── Queue health ──────────────────────────────────────────────────────


class TestQueueHealth:
    async def test_empty_health(self, queue: JobQueue):
        health = await queue.get_health()
        assert health.pending == 0
        assert health.processing == 0
        assert health.dead_letter == 0

    async def test_health_counts(self, queue: JobQueue, fake_redis: FakeRedis):
        await queue.enqueue("a", {})
        await queue.enqueue("b", {})
        await queue.dequeue(timeout=1)  # moves 1 to processing

        health = await queue.get_health()
        assert health.pending == 1
        assert health.processing == 1


# ── Queue size ─────────────────────────────────────────────────────────


class TestQueueSize:
    async def test_queue_size(self, queue: JobQueue):
        assert await queue.queue_size() == 0
        await queue.enqueue("a", {})
        await queue.enqueue("b", {})
        assert await queue.queue_size() == 2


# ── Worker integration ─────────────────────────────────────────────────


class TestWorkerIntegration:
    """Tests that exercise Worker._process_job with the hardened queue."""

    async def test_successful_job_is_acked(self, queue: JobQueue, fake_redis: FakeRedis):
        """End-to-end: enqueue → dequeue → handler succeeds → ack."""
        job_id = await queue.enqueue("test_job", {"val": 1})
        envelope = await queue.dequeue(timeout=1)

        # Simulate successful processing
        await queue.ack(envelope.job_id, {"result": "ok"})

        state = await queue.get_status(job_id)
        assert state.status == "completed"
        assert '"ok"' in state.result

    async def test_failed_job_retries_then_dies(self, queue: JobQueue, fake_redis: FakeRedis):
        """End-to-end: job fails max_retries times → lands in DLQ."""
        job_id = await queue.enqueue("failing_job", {}, max_retries=2)

        for i in range(2):
            envelope = await queue.dequeue(timeout=1)
            assert envelope is not None
            if i < 1:
                with patch("app.queue.asyncio.sleep", new_callable=AsyncMock):
                    await queue.nack(envelope, f"error {i}")
            else:
                await queue.nack(envelope, f"error {i}")

        state = await queue.get_status(job_id)
        assert state.status == "dead"
        assert await fake_redis.llen(DLQ_KEY) == 1

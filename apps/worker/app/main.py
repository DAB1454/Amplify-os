"""Redis-based queue consumer for Amplify OS worker."""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any, Callable, Coroutine

import redis.asyncio as redis

from app.config import Settings
from app.queue import JobEnvelope, JobQueue
from app.scheduler import Scheduler
from app.jobs.ingest_calendar import ingest_calendar
from app.jobs.generate_content import generate_content
from app.jobs.render_media import render_media
from app.jobs.publish_post import publish_post
from app.jobs.sync_metrics import sync_metrics
from app.jobs.moderate_comments import moderate_comments
from app.jobs.run_experiment_loop import run_experiment_loop
from app.jobs.send_alerts import send_alerts
from app.jobs.scan_scheduled import scan_scheduled
from app.jobs.weekly_analyst import weekly_analyst
from app.jobs.backfill_learning import backfill_learning
from app.jobs.intelligence_jobs import (
    extract_features,
    compute_rewards,
    update_tenant_patterns,
    aggregate_cohorts,
    run_evaluation,
    check_model_health,
)

logger = logging.getLogger(__name__)

JOB_HANDLERS: dict[str, Callable[[dict], Coroutine[Any, Any, dict]]] = {
    "ingest_calendar": ingest_calendar,
    "generate_content": generate_content,
    "render_media": render_media,
    "publish_post": publish_post,
    "sync_metrics": sync_metrics,
    "moderate_comments": moderate_comments,
    "run_experiment_loop": run_experiment_loop,
    "send_alerts": send_alerts,
    "scan_scheduled": scan_scheduled,
    "weekly_analyst": weekly_analyst,
    "backfill_learning": backfill_learning,
    # Intelligence pipeline jobs
    "extract_features": extract_features,
    "compute_rewards": compute_rewards,
    "update_tenant_patterns": update_tenant_patterns,
    "aggregate_cohorts": aggregate_cohorts,
    "run_evaluation": run_evaluation,
    "check_model_health": check_model_health,
}


class Worker:
    """Main worker process that consumes jobs from a Redis queue.

    Uses the production JobQueue with retries, DLQ, dedup, and stuck-job recovery.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self._running = False
        self._redis: redis.Redis | None = None
        self._queue: JobQueue | None = None
        self._scheduler: Scheduler | None = None

    async def _connect(self) -> None:
        self._redis = redis.from_url(self.settings.redis_url, decode_responses=True)
        self._queue = JobQueue(self._redis)
        logger.info("Connected to Redis at %s", self.settings.redis_url)

    async def _disconnect(self) -> None:
        if self._scheduler:
            await self._scheduler.stop()
        if self._redis:
            await self._redis.aclose()
            logger.info("Disconnected from Redis")

    async def _process_job(self, envelope: JobEnvelope) -> None:
        handler = JOB_HANDLERS.get(envelope.job_type)
        if handler is None:
            logger.warning("Unknown job_type=%s (job_id=%s)", envelope.job_type, envelope.job_id)
            assert self._queue is not None
            await self._queue.ack(envelope.job_id)
            return

        logger.info("Processing job_id=%s type=%s attempt=%d", envelope.job_id, envelope.job_type, envelope.attempt)
        assert self._queue is not None

        try:
            result = await asyncio.wait_for(
                handler(envelope.payload),
                timeout=envelope.timeout_seconds,
            )
            await self._queue.ack(envelope.job_id, result)
            logger.info("Completed job_id=%s", envelope.job_id)
        except asyncio.TimeoutError:
            error = f"Job timed out after {envelope.timeout_seconds}s"
            logger.error("Timeout job_id=%s type=%s: %s", envelope.job_id, envelope.job_type, error)
            await self._queue.nack(envelope, error)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.exception("Failed job_id=%s type=%s", envelope.job_id, envelope.job_type)
            await self._queue.nack(envelope, error)

    async def _start_scheduler(self) -> None:
        """Set up and start periodic background tasks."""
        assert self._queue is not None
        self._scheduler = Scheduler()

        async def recover_stuck() -> None:
            recovered = await self._queue.recover_stuck_jobs(
                max_age_seconds=self.settings.job_timeout,
            )
            if recovered:
                logger.info("Recovered %d stuck jobs", recovered)

        self._scheduler.register(recover_stuck, self.settings.stuck_job_check_interval)

        # ── Intelligence pipeline schedules ──────────────────────────
        # Feature extraction — nightly (every 24h)
        async def scheduled_extract_features() -> None:
            await self._queue.enqueue("extract_features", {})

        # Reward computation — every 4 hours
        async def scheduled_compute_rewards() -> None:
            await self._queue.enqueue("compute_rewards", {})

        # Tenant pattern update — nightly (every 24h)
        async def scheduled_update_patterns() -> None:
            await self._queue.enqueue("update_tenant_patterns", {})

        # Cohort aggregation — weekly (every 7 days)
        async def scheduled_aggregate_cohorts() -> None:
            await self._queue.enqueue("aggregate_cohorts", {})

        # Model health check — every 6 hours
        async def scheduled_health_check() -> None:
            await self._queue.enqueue("check_model_health", {})

        self._scheduler.register(scheduled_extract_features, 86400)       # 24h
        self._scheduler.register(scheduled_compute_rewards, 14400)        # 4h
        self._scheduler.register(scheduled_update_patterns, 86400)        # 24h
        self._scheduler.register(scheduled_aggregate_cohorts, 604800)     # 7d
        self._scheduler.register(scheduled_health_check, 21600)           # 6h

        await self._scheduler.start()

    async def run(self) -> None:
        """Main loop: dequeue jobs and dispatch to handlers."""
        await self._connect()
        self._running = True
        assert self._queue is not None

        await self._start_scheduler()
        logger.info("Worker started, listening for jobs")

        try:
            while self._running:
                envelope = await self._queue.dequeue(timeout=2)
                if envelope is None:
                    continue
                await self._process_job(envelope)
        finally:
            await self._disconnect()

    def stop(self) -> None:
        logger.info("Shutdown requested")
        self._running = False


async def main() -> None:
    settings = Settings()
    logging.basicConfig(level=settings.log_level)

    worker = Worker(settings)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, worker.stop)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            signal.signal(sig, lambda _s, _f: worker.stop())

    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())

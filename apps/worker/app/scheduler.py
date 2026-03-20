"""Simple periodic job scheduler using asyncio."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Coroutine, Any

logger = logging.getLogger(__name__)


class Scheduler:
    """Register and run periodic async jobs at fixed intervals."""

    def __init__(self) -> None:
        self._jobs: list[tuple[Callable[[], Coroutine[Any, Any, Any]], float]] = []
        self._tasks: list[asyncio.Task] = []
        self._running = False

    def register(self, job_fn: Callable[[], Coroutine[Any, Any, Any]], interval_seconds: float) -> None:
        """Register a periodic job to run every *interval_seconds*."""
        self._jobs.append((job_fn, interval_seconds))
        logger.info("Registered job %s every %.1fs", job_fn.__name__, interval_seconds)

    async def _run_loop(self, job_fn: Callable[[], Coroutine[Any, Any, Any]], interval: float) -> None:
        while self._running:
            try:
                await job_fn()
            except Exception:
                logger.exception("Scheduled job %s failed", job_fn.__name__)
            await asyncio.sleep(interval)

    async def start(self) -> None:
        """Start all registered periodic jobs."""
        self._running = True
        for job_fn, interval in self._jobs:
            task = asyncio.create_task(self._run_loop(job_fn, interval))
            self._tasks.append(task)
        logger.info("Scheduler started with %d jobs", len(self._tasks))

    async def stop(self) -> None:
        """Cancel all running periodic jobs."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("Scheduler stopped")

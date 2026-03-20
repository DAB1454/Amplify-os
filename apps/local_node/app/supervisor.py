"""Process supervisor with auto-restart and exponential backoff."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Coroutine, Any

logger = logging.getLogger(__name__)


class ProcessState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    BACKOFF = "backoff"
    FATAL = "fatal"


@dataclass
class ProcessInfo:
    """Runtime state for a supervised process."""

    name: str
    state: ProcessState = ProcessState.STOPPED
    restart_count: int = 0
    last_start: datetime | None = None
    last_exit: datetime | None = None
    last_error: str | None = None
    task: asyncio.Task | None = field(default=None, repr=False)


class Supervisor:
    """Manages a set of async coroutines with auto-restart on failure.

    Each registered process is wrapped in a restart loop. On crash, the
    supervisor waits with exponential backoff before restarting, up to
    max_attempts. After max_attempts consecutive failures, the process
    enters FATAL state and stops restarting.

    A process that runs for more than 60 seconds before crashing resets
    the failure counter (it's considered a "successful" run that later
    failed, not a startup crash).
    """

    HEALTHY_RUNTIME_SECS = 60

    def __init__(
        self,
        max_attempts: int = 10,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
    ) -> None:
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._processes: dict[str, tuple[Callable[[], Coroutine[Any, Any, None]], ProcessInfo]] = {}
        self._running = False

    def register(
        self,
        name: str,
        coro_factory: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        """Register a coroutine factory to be supervised."""
        self._processes[name] = (coro_factory, ProcessInfo(name=name))
        logger.info("Registered process: %s", name)

    async def _supervise(
        self,
        name: str,
        coro_factory: Callable[[], Coroutine[Any, Any, None]],
        info: ProcessInfo,
    ) -> None:
        """Run a process in a restart loop with backoff."""
        consecutive_failures = 0

        while self._running:
            info.state = ProcessState.STARTING
            info.last_start = datetime.now(timezone.utc)
            info.restart_count += 1

            try:
                logger.info("[%s] Starting (attempt %d)", name, info.restart_count)
                info.state = ProcessState.RUNNING
                await coro_factory()
                # Clean exit — process completed normally
                logger.info("[%s] Exited normally", name)
                info.last_exit = datetime.now(timezone.utc)
                info.state = ProcessState.STOPPED
                break

            except asyncio.CancelledError:
                logger.info("[%s] Cancelled", name)
                info.state = ProcessState.STOPPED
                break

            except Exception as exc:
                info.last_exit = datetime.now(timezone.utc)
                info.last_error = str(exc)
                logger.error("[%s] Crashed: %s", name, exc)

                # If process ran for a while, reset failure counter
                runtime = (info.last_exit - info.last_start).total_seconds()
                if runtime >= self.HEALTHY_RUNTIME_SECS:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1

                if consecutive_failures >= self._max_attempts:
                    logger.error(
                        "[%s] Reached %d consecutive failures — entering FATAL state",
                        name,
                        self._max_attempts,
                    )
                    info.state = ProcessState.FATAL
                    break

                # Exponential backoff
                delay = min(
                    self._backoff_base * (2 ** (consecutive_failures - 1)),
                    self._backoff_max,
                )
                info.state = ProcessState.BACKOFF
                logger.info("[%s] Restarting in %.1fs", name, delay)
                await asyncio.sleep(delay)

    async def start(self) -> None:
        """Start all registered processes under supervision."""
        self._running = True
        for name, (factory, info) in self._processes.items():
            info.task = asyncio.create_task(self._supervise(name, factory, info))
        logger.info("Supervisor started with %d processes", len(self._processes))

    async def stop(self) -> None:
        """Gracefully stop all processes."""
        self._running = False
        tasks = []
        for _name, (_factory, info) in self._processes.items():
            if info.task and not info.task.done():
                info.task.cancel()
                tasks.append(info.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Supervisor stopped")

    def status(self) -> dict[str, dict]:
        """Return a snapshot of all process states."""
        result = {}
        for name, (_factory, info) in self._processes.items():
            result[name] = {
                "state": info.state.value,
                "restart_count": info.restart_count,
                "last_start": info.last_start.isoformat() if info.last_start else None,
                "last_exit": info.last_exit.isoformat() if info.last_exit else None,
                "last_error": info.last_error,
            }
        return result

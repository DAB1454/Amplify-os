"""Stub tracing module for Amplify-OS.

Provides a ``@trace`` decorator and a ``SpanContext`` class for manual
span management.  Currently logs entry/exit with elapsed time via
structlog; a future iteration will export to OpenTelemetry / Jaeger.
"""

from __future__ import annotations

import functools
import time
from contextlib import contextmanager
from typing import Any, Callable, Generator

import structlog

logger = structlog.get_logger("tracing")


# ------------------------------------------------------------------
# Decorator
# ------------------------------------------------------------------

def trace(name: str) -> Callable:
    """Decorator that logs entry and exit with timing for a function.

    Works with both sync and async functions.

    Usage::

        @trace("campaign.create")
        async def create_campaign(...):
            ...
    """

    def decorator(fn: Callable) -> Callable:
        import asyncio

        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                logger.info("span.start", span=name)
                try:
                    result = await fn(*args, **kwargs)
                    elapsed = (time.perf_counter() - start) * 1000
                    logger.info("span.end", span=name, elapsed_ms=round(elapsed, 2))
                    return result
                except Exception:
                    elapsed = (time.perf_counter() - start) * 1000
                    logger.error("span.error", span=name, elapsed_ms=round(elapsed, 2))
                    raise

            return _async_wrapper
        else:

            @functools.wraps(fn)
            def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                logger.info("span.start", span=name)
                try:
                    result = fn(*args, **kwargs)
                    elapsed = (time.perf_counter() - start) * 1000
                    logger.info("span.end", span=name, elapsed_ms=round(elapsed, 2))
                    return result
                except Exception:
                    elapsed = (time.perf_counter() - start) * 1000
                    logger.error("span.error", span=name, elapsed_ms=round(elapsed, 2))
                    raise

            return _sync_wrapper

    return decorator


# ------------------------------------------------------------------
# Manual span context
# ------------------------------------------------------------------

class SpanContext:
    """Lightweight context-manager for manual span tracking.

    Usage::

        with SpanContext("db.query") as span:
            rows = await db.fetch(...)
            span.set_attribute("row_count", len(rows))
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[str, Any] = {}
        self._start: float = 0

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def __enter__(self) -> SpanContext:
        self._start = time.perf_counter()
        logger.info("span.start", span=self.name)
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: Any) -> None:
        elapsed = (time.perf_counter() - self._start) * 1000
        if exc_type is not None:
            logger.error(
                "span.error",
                span=self.name,
                elapsed_ms=round(elapsed, 2),
                error=str(exc_val),
                **self.attributes,
            )
        else:
            logger.info(
                "span.end",
                span=self.name,
                elapsed_ms=round(elapsed, 2),
                **self.attributes,
            )

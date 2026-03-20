"""Simple in-memory metrics collector stub.

TODO: Add export to Prometheus / DataDog / CloudWatch.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class _MetricPoint:
    name: str
    value: float
    tags: dict[str, str]
    timestamp: float


class MetricsCollector:
    """Collects counters, gauges, and histograms in memory.

    Thread-safe via a simple lock.  In production this would flush
    periodically to an external metrics backend.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}
        self._points: list[_MetricPoint] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def increment(self, name: str, tags: dict[str, str] | None = None) -> None:
        """Increment a counter by 1."""
        key = self._key(name, tags)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1
            self._points.append(
                _MetricPoint(name=name, value=self._counters[key], tags=tags or {}, timestamp=time.time())
            )

    def gauge(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Set a gauge to an absolute value."""
        key = self._key(name, tags)
        with self._lock:
            self._gauges[key] = value
            self._points.append(
                _MetricPoint(name=name, value=value, tags=tags or {}, timestamp=time.time())
            )

    def histogram(self, name: str, value: float, tags: dict[str, str] | None = None) -> None:
        """Record an observation for a histogram/distribution."""
        key = self._key(name, tags)
        with self._lock:
            self._histograms.setdefault(key, []).append(value)
            self._points.append(
                _MetricPoint(name=name, value=value, tags=tags or {}, timestamp=time.time())
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _key(name: str, tags: dict[str, str] | None) -> str:
        if not tags:
            return name
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}[{tag_str}]"

    def snapshot(self) -> dict[str, dict[str, float] | dict[str, list[float]]]:
        """Return a copy of current metric state (useful for tests)."""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {k: list(v) for k, v in self._histograms.items()},
            }

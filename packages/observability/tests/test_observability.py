"""Tests for observability modules: metrics, tracing, alerts, logging."""

from __future__ import annotations

import pytest

from amplify.observability.metrics import MetricsCollector
from amplify.observability.tracing import trace, SpanContext
from amplify.observability.alerts import AlertManager, SEVERITY_LEVELS
from amplify.observability.logging import setup_logging, get_logger


# ── MetricsCollector ─────────────────────────────────────────────────


class TestMetricsCollector:
    def test_increment_counter(self):
        m = MetricsCollector()
        m.increment("requests")
        m.increment("requests")
        snap = m.snapshot()
        assert snap["counters"]["requests"] == 2

    def test_increment_with_tags(self):
        m = MetricsCollector()
        m.increment("requests", tags={"method": "GET"})
        m.increment("requests", tags={"method": "POST"})
        m.increment("requests", tags={"method": "GET"})
        snap = m.snapshot()
        assert snap["counters"]["requests[method=GET]"] == 2
        assert snap["counters"]["requests[method=POST]"] == 1

    def test_gauge(self):
        m = MetricsCollector()
        m.gauge("cpu", 45.5)
        m.gauge("cpu", 72.1)
        snap = m.snapshot()
        assert snap["gauges"]["cpu"] == 72.1  # last value wins

    def test_histogram(self):
        m = MetricsCollector()
        m.histogram("latency", 10.5)
        m.histogram("latency", 20.3)
        m.histogram("latency", 15.1)
        snap = m.snapshot()
        assert snap["histograms"]["latency"] == [10.5, 20.3, 15.1]

    def test_snapshot_is_isolated(self):
        m = MetricsCollector()
        m.increment("x")
        snap1 = m.snapshot()
        m.increment("x")
        snap2 = m.snapshot()
        assert snap1["counters"]["x"] == 1
        assert snap2["counters"]["x"] == 2

    def test_empty_snapshot(self):
        m = MetricsCollector()
        snap = m.snapshot()
        assert snap == {"counters": {}, "gauges": {}, "histograms": {}}

    def test_tags_sorted_in_key(self):
        m = MetricsCollector()
        m.increment("x", tags={"b": "2", "a": "1"})
        snap = m.snapshot()
        assert "x[a=1,b=2]" in snap["counters"]


# ── Tracing ──────────────────────────────────────────────────────────


class TestTrace:
    def test_sync_function_traced(self):
        @trace("test.sync")
        def add(a: int, b: int) -> int:
            return a + b

        assert add(1, 2) == 3

    @pytest.mark.asyncio
    async def test_async_function_traced(self):
        @trace("test.async")
        async def add(a: int, b: int) -> int:
            return a + b

        assert await add(1, 2) == 3

    def test_sync_function_reraises(self):
        @trace("test.error")
        def boom() -> None:
            raise ValueError("kaboom")

        with pytest.raises(ValueError, match="kaboom"):
            boom()

    @pytest.mark.asyncio
    async def test_async_function_reraises(self):
        @trace("test.async_error")
        async def boom() -> None:
            raise RuntimeError("async kaboom")

        with pytest.raises(RuntimeError, match="async kaboom"):
            await boom()


class TestSpanContext:
    def test_span_as_context_manager(self):
        with SpanContext("test.span") as span:
            span.set_attribute("key", "value")
        assert span.attributes["key"] == "value"

    def test_span_propagates_exception(self):
        with pytest.raises(ValueError):
            with SpanContext("test.error_span"):
                raise ValueError("inside span")


# ── AlertManager ─────────────────────────────────────────────────────


class TestAlertManager:
    @pytest.mark.asyncio
    async def test_send_alert_info(self):
        am = AlertManager()
        # Should not raise
        await am.send_alert("info", "Test", "This is a test alert")

    @pytest.mark.asyncio
    async def test_send_alert_warning(self):
        am = AlertManager()
        await am.send_alert("warning", "Disk High", "Disk at 85%")

    @pytest.mark.asyncio
    async def test_send_alert_critical(self):
        am = AlertManager()
        await am.send_alert("critical", "DB Down", "Cannot connect to postgres")

    @pytest.mark.asyncio
    async def test_invalid_severity_raises(self):
        am = AlertManager()
        with pytest.raises(ValueError, match="Invalid severity"):
            await am.send_alert("panic", "Title", "Message")

    def test_severity_levels_tuple(self):
        assert SEVERITY_LEVELS == ("info", "warning", "critical")


# ── Logging ──────────────────────────────────────────────────────────


class TestLogging:
    def test_setup_logging_json(self):
        setup_logging(level="DEBUG", json_output=True)
        logger = get_logger("test")
        assert logger is not None

    def test_setup_logging_console(self):
        setup_logging(level="INFO", json_output=False)
        logger = get_logger("test_console")
        assert logger is not None

    def test_get_logger_returns_bound_logger(self):
        logger = get_logger("my_module")
        assert hasattr(logger, "info")
        assert hasattr(logger, "error")
        assert hasattr(logger, "debug")

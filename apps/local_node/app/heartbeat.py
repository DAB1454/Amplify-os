"""Heartbeat: both a local HTTP endpoint and an API registration client."""

from __future__ import annotations

import logging
import platform
from datetime import datetime, timezone

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

logger = logging.getLogger(__name__)


# ── Outbound heartbeat client ────────────────────────────────────────


class HeartbeatClient:
    """Sends periodic heartbeats to the Amplify API."""

    def __init__(self) -> None:
        self.last_success: datetime | None = None
        self.last_failure: datetime | None = None
        self.consecutive_failures: int = 0

    async def send(self, api_url: str, node_id: str, capabilities: list[str]) -> bool:
        """POST heartbeat to API. Returns True on success."""
        payload = {
            "node_id": node_id,
            "capabilities": capabilities,
            "system": {
                "os": platform.system(),
                "arch": platform.machine(),
                "python": platform.python_version(),
                "hostname": platform.node(),
            },
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{api_url}/api/v1/nodes/heartbeat",
                    json=payload,
                    timeout=10,
                )
                if resp.status_code == 200:
                    self.last_success = datetime.now(timezone.utc)
                    self.consecutive_failures = 0
                    return True
                self.consecutive_failures += 1
                self.last_failure = datetime.now(timezone.utc)
                return False
        except Exception:
            self.consecutive_failures += 1
            self.last_failure = datetime.now(timezone.utc)
            logger.debug("Heartbeat failed (attempt %d)", self.consecutive_failures)
            return False

    @property
    def api_reachable(self) -> bool:
        return self.consecutive_failures == 0


# ── Inbound heartbeat server (health endpoint) ──────────────────────


def create_heartbeat_app(
    node_id: str,
    supervisor_status_fn: callable,
    offline_queue_size_fn: callable,
) -> Starlette:
    """Create a lightweight Starlette app for the heartbeat endpoint."""

    startup_time = datetime.now(timezone.utc)

    async def health(request: Request) -> JSONResponse:
        procs = supervisor_status_fn()
        return JSONResponse({
            "status": "ok",
            "node_id": node_id,
            "uptime_seconds": (datetime.now(timezone.utc) - startup_time).total_seconds(),
            "processes": procs,
            "offline_queue_size": offline_queue_size_fn(),
            "system": {
                "os": platform.system(),
                "arch": platform.machine(),
                "hostname": platform.node(),
            },
        })

    async def ready(request: Request) -> JSONResponse:
        procs = supervisor_status_fn()
        all_running = all(p["state"] == "running" for p in procs.values())
        status_code = 200 if all_running else 503
        return JSONResponse(
            {"ready": all_running, "processes": procs},
            status_code=status_code,
        )

    return Starlette(
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/ready", ready, methods=["GET"]),
        ],
    )

"""Request tracing middleware — adds correlation IDs to every request."""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class TracingMiddleware(BaseHTTPMiddleware):
    """Generates a unique request_id per request.

    - Binds request_id to structlog context vars so all log lines include it.
    - Returns X-Request-ID header in the response.
    - Logs request completion with method, path, status, and elapsed time.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        logger = structlog.get_logger("http")
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            elapsed = (time.perf_counter() - start) * 1000
            logger.error("request.error", elapsed_ms=round(elapsed, 1))
            raise

        elapsed = (time.perf_counter() - start) * 1000
        logger.info(
            "request.complete",
            status=response.status_code,
            elapsed_ms=round(elapsed, 1),
        )

        response.headers["X-Request-ID"] = request_id
        return response

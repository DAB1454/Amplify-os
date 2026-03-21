"""Tenant isolation middleware.

In local mode: sets tenant_id to a fixed "local" UUID on every request.
In cloud mode: extracts tenant_id from the validated JWT in the Authorization header.
Skips auth for: /healthz, /docs, /openapi.json, /api/v1/auth/* endpoints.
"""

from __future__ import annotations

import uuid

import jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# Fixed UUID for local single-tenant mode
LOCAL_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
LOCAL_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

# Paths that skip auth entirely
SKIP_AUTH_PATHS = {"/health", "/healthz", "/docs", "/redoc", "/openapi.json"}
SKIP_AUTH_PREFIXES = ("/api/v1/auth/", "/r/")


class TenantMiddleware(BaseHTTPMiddleware):
    """Injects tenant context into every request."""

    def __init__(self, app, deployment_mode: str, jwt_secret: str, jwt_algorithm: str = "HS256"):
        super().__init__(app)
        self.deployment_mode = deployment_mode
        self.jwt_secret = jwt_secret
        self.jwt_algorithm = jwt_algorithm
        self.is_local = deployment_mode == "local"

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Skip auth for public endpoints
        if path in SKIP_AUTH_PATHS or any(path.startswith(p) for p in SKIP_AUTH_PREFIXES):
            request.state.tenant_id = LOCAL_TENANT_ID if self.is_local else None
            request.state.user_id = LOCAL_USER_ID if self.is_local else None
            request.state.role = "owner" if self.is_local else None
            return await call_next(request)

        if self.is_local:
            # Local mode: no auth required, fixed tenant
            request.state.tenant_id = LOCAL_TENANT_ID
            request.state.user_id = LOCAL_USER_ID
            request.state.role = "owner"
            return await call_next(request)

        # Cloud mode: validate JWT
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header"},
            )

        token = authorization.removeprefix("Bearer ")
        try:
            payload = jwt.decode(token, self.jwt_secret, algorithms=[self.jwt_algorithm])
        except jwt.ExpiredSignatureError:
            return JSONResponse(status_code=401, content={"detail": "Token expired"})
        except jwt.PyJWTError:
            return JSONResponse(status_code=401, content={"detail": "Invalid token"})

        if payload.get("type") != "access":
            return JSONResponse(status_code=401, content={"detail": "Invalid token type"})

        request.state.tenant_id = uuid.UUID(payload["tenant_id"])
        request.state.user_id = uuid.UUID(payload["sub"])
        request.state.role = payload.get("role", "member")

        return await call_next(request)

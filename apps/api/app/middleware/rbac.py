"""Role-based access control."""

from __future__ import annotations

from functools import wraps
from typing import Callable

from fastapi import HTTPException, Request


# Role hierarchy: owner > admin > member
ROLE_HIERARCHY = {"owner": 3, "admin": 2, "member": 1}


def require_role(minimum_role: str) -> Callable:
    """FastAPI dependency that checks the user has at least the given role.

    Usage:
        @router.post("/", dependencies=[Depends(require_role("admin"))])
        async def create_thing(...):
            ...
    """
    min_level = ROLE_HIERARCHY.get(minimum_role, 0)

    async def check_role(request: Request) -> None:
        role = getattr(request.state, "role", None)
        if role is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        user_level = ROLE_HIERARCHY.get(role, 0)
        if user_level < min_level:
            raise HTTPException(
                status_code=403,
                detail=f"Requires {minimum_role} role or higher",
            )

    return check_role

"""Audit logging service."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.audit_log import AuditLogModel


def _json_safe(obj: Any) -> Any:
    """Recursively convert non-JSON-serializable types (UUID, date, etc.)."""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


class AuditService:
    """Records audit log entries for tenant actions."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID | str):
        self.session = session
        self.tenant_id = uuid.UUID(str(tenant_id))

    async def log(
        self,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        changes: dict[str, Any] | None = None,
        ip_address: str | None = None,
    ) -> AuditLogModel:
        """Create an audit log entry."""
        entry = AuditLogModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            changes=_json_safe(changes or {}),
            ip_address=ip_address,
        )
        self.session.add(entry)
        await self.session.flush()
        # Refresh so server-generated columns (created_at, updated_at) are
        # loaded before any downstream serialization — avoids MissingGreenlet.
        await self.session.refresh(entry)
        return entry

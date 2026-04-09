"""AutomationAuditService — records the agent decision trail.

Distinct from AuditService (which records human-initiated changes for
compliance). Every worker tick, every decider call, every auto-approval
or escalation writes a row here so the user can see exactly what the
autonomous agent did while they were away.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.automation_audit import (
    AUTOMATION_ACTIONS,
    AUTOMATION_OUTCOMES,
    AutomationAuditModel,
)


def _json_safe(obj: Any) -> Any:
    """Recursively convert non-JSON-serializable types (UUID, date, etc.).

    Local copy of the helper in ``app/services/audit_service.py``. Kept
    here so this module has zero dependencies on the API package and can
    be imported by both the API and the worker.
    """
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


class AutomationAuditService:
    """Records autonomous-agent decisions for the current tenant."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID | str):
        self.session = session
        self.tenant_id = uuid.UUID(str(tenant_id))

    async def record(
        self,
        action: str,
        *,
        reason: str = "",
        confidence: float | None = None,
        outcome: str = "success",
        error: str | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        snapshot: dict[str, Any] | None = None,
    ) -> AutomationAuditModel:
        """Write one automation audit row.

        Validates ``action`` and ``outcome`` against the closed sets in
        the model module so a typo in the worker becomes a loud error
        instead of a silent garbage row.
        """
        if action not in AUTOMATION_ACTIONS:
            raise ValueError(
                f"unknown automation action {action!r}; "
                f"must be one of {AUTOMATION_ACTIONS}"
            )
        if outcome not in AUTOMATION_OUTCOMES:
            raise ValueError(
                f"unknown automation outcome {outcome!r}; "
                f"must be one of {AUTOMATION_OUTCOMES}"
            )

        entry = AutomationAuditModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            action=action,
            reason=reason or "",
            confidence=confidence,
            outcome=outcome,
            error=error,
            entity_type=entity_type,
            entity_id=entity_id,
            snapshot=_json_safe(snapshot or {}),
        )
        self.session.add(entry)
        await self.session.flush()
        await self.session.refresh(entry)
        return entry

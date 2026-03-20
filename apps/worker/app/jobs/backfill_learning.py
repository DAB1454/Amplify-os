"""Worker job entry point for learning backfill.

The heavy lifting is in app.services.backfill_service.BackfillEngine.
This module provides the thin worker-compatible handler.

Payload schema:
{
    "tenant_id": "uuid",                   # required
    "batch_size": 200,                     # posts per batch (default 200)
    "skip_features": false,                # skip feature extraction
    "skip_outcomes": false,                # skip outcome backfill
    "skip_events": false,                  # skip learning event emission
    "bootstrap_patterns": true,            # run pattern learning after backfill
    "cursor": null,                        # resume cursor (post_id string)
    "dry_run": false                       # report-only, no writes
}
"""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


async def backfill_learning(payload: dict) -> dict:
    """Worker job handler for learning backfill."""
    from amplify.db.session import get_async_session
    from worker.app.config import Settings

    settings = Settings()
    tenant_id_str = payload.get("tenant_id")
    if not tenant_id_str:
        return {"status": "error", "error": "tenant_id is required"}

    tenant_id = uuid.UUID(tenant_id_str)

    async with get_async_session(settings.database_url) as db:
        from amplify.learning.backfill import BackfillEngine

        engine = BackfillEngine(
            db, tenant_id,
            dry_run=payload.get("dry_run", False),
        )
        report = await engine.run(
            batch_size=payload.get("batch_size", 200),
            skip_features=payload.get("skip_features", False),
            skip_outcomes=payload.get("skip_outcomes", False),
            skip_events=payload.get("skip_events", False),
            bootstrap_patterns=payload.get("bootstrap_patterns", True),
            cursor=payload.get("cursor"),
        )

    return {"status": report.status, "report": report.to_dict()}

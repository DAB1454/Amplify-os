"""Sweep for orphaned posts (channel_id IS NULL) older than a threshold.

Posts can become orphaned when a user disconnects a channel but never
completes the reconnect OAuth flow (browser crash, OAuth deny, etc).
Once orphaned, they're invisible in the UI but still stored — and the
reconnect path will only re-associate them on a successful OAuth callback.

This sweeper logs a warning per (tenant, platform) pair so operators can
notice the half-disconnected state and reach out, or so a future alerting
hook can fire on it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import text

logger = logging.getLogger(__name__)

# How stale an orphan must be before we flag it. Less than this and we assume
# the user is mid-reconnect.
STALE_AFTER = timedelta(hours=24)


async def sweep_orphaned_posts(payload: dict | None = None) -> dict:
    """Find and log tenants with orphaned posts older than 24h."""
    from amplify.db.session import get_async_session
    from app.config import Settings

    settings = Settings()
    cutoff = datetime.utcnow() - STALE_AFTER
    flagged: list[dict] = []

    async with get_async_session(settings.database_url) as db:
        result = await db.execute(
            text(
                "SELECT tenant_id, platform, COUNT(*) AS n, "
                "       MIN(updated_at) AS oldest "
                "FROM posts "
                "WHERE channel_id IS NULL "
                "  AND updated_at < :cutoff "
                "GROUP BY tenant_id, platform "
                "ORDER BY n DESC"
            ),
            {"cutoff": cutoff},
        )
        for row in result:
            flagged.append({
                "tenant_id": str(row.tenant_id),
                "platform": row.platform,
                "orphaned_count": row.n,
                "oldest_orphan": row.oldest.isoformat() if row.oldest else None,
            })
            logger.warning(
                "Orphaned posts: tenant=%s platform=%s count=%d oldest=%s "
                "(user likely disconnected and never finished reconnecting)",
                row.tenant_id, row.platform, row.n, row.oldest,
            )

    return {
        "status": "ok",
        "checked_at": datetime.utcnow().isoformat(),
        "stale_threshold_hours": int(STALE_AFTER.total_seconds() / 3600),
        "flagged_groups": len(flagged),
        "details": flagged,
    }

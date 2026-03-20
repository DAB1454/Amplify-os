"""Calendar ingestion job."""

from __future__ import annotations


async def ingest_calendar(payload: dict) -> dict:
    """Pull calendar events from connected platforms and sync to campaign timeline."""
    # TODO: implement calendar sync logic
    return {"status": "ok", "events_synced": 0}

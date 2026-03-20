"""SQLite-backed offline queue for buffering jobs during API outages."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".amplify-os" / "offline_queue.db"


@dataclass
class QueuedJob:
    """A job stored in the offline queue."""

    job_id: str
    job_type: str
    payload: dict
    created_at: str
    attempts: int = 0
    last_error: str | None = None


class OfflineQueue:
    """SQLite-backed persistent queue for surviving API outages.

    When the API is unreachable, jobs are written to local SQLite instead
    of being dropped. A flush loop periodically retries sending them
    once connectivity is restored.
    """

    def __init__(self, db_path: Path | None = None, max_size: int = 1000) -> None:
        self._db_path = db_path or DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._max_size = max_size
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_table()

    def _create_table(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS offline_jobs (
                job_id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                attempts INTEGER DEFAULT 0,
                last_error TEXT
            )
        """)
        self._conn.commit()

    def enqueue(self, job_type: str, payload: dict) -> str:
        """Add a job to the offline queue. Returns job_id."""
        if self.size() >= self._max_size:
            logger.warning("Offline queue full (%d items), dropping oldest", self._max_size)
            self._conn.execute("""
                DELETE FROM offline_jobs WHERE job_id IN (
                    SELECT job_id FROM offline_jobs ORDER BY created_at ASC LIMIT 1
                )
            """)

        job_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO offline_jobs (job_id, job_type, payload, created_at) VALUES (?, ?, ?, ?)",
            (job_id, job_type, json.dumps(payload), now),
        )
        self._conn.commit()
        logger.info("Queued offline job %s (type=%s)", job_id, job_type)
        return job_id

    def peek(self, limit: int = 10) -> list[QueuedJob]:
        """Return the oldest N jobs without removing them."""
        rows = self._conn.execute(
            "SELECT job_id, job_type, payload, created_at, attempts, last_error "
            "FROM offline_jobs ORDER BY created_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            QueuedJob(
                job_id=r[0],
                job_type=r[1],
                payload=json.loads(r[2]),
                created_at=r[3],
                attempts=r[4],
                last_error=r[5],
            )
            for r in rows
        ]

    def ack(self, job_id: str) -> None:
        """Remove a job after successful delivery."""
        self._conn.execute("DELETE FROM offline_jobs WHERE job_id = ?", (job_id,))
        self._conn.commit()

    def nack(self, job_id: str, error: str) -> None:
        """Record a failed delivery attempt."""
        self._conn.execute(
            "UPDATE offline_jobs SET attempts = attempts + 1, last_error = ? WHERE job_id = ?",
            (error, job_id),
        )
        self._conn.commit()

    def size(self) -> int:
        """Return the number of jobs in the queue."""
        row = self._conn.execute("SELECT COUNT(*) FROM offline_jobs").fetchone()
        return row[0] if row else 0

    def clear(self) -> int:
        """Remove all jobs. Returns the count removed."""
        count = self.size()
        self._conn.execute("DELETE FROM offline_jobs")
        self._conn.commit()
        return count

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

"""Mark the 9 stuck TikTok posts (status=publishing, blocked by token-refresh
dedup) as failed so the user can re-edit/republish manually.

Run: python scripts/fail_stuck_tiktok_posts.py [--apply]
Without --apply it does a dry run.
"""
import asyncio
import os
import sys

import asyncpg


SELECT_SQL = """
SELECT id, scheduled_at, retry_count, LEFT(COALESCE(content_text, ''), 60) AS content
FROM posts
WHERE platform = 'tiktok'
  AND status = 'publishing'
  AND last_error LIKE 'Token expired for tiktok%'
ORDER BY scheduled_at
"""

UPDATE_SQL = """
UPDATE posts
SET status = 'failed',
    last_error = 'TikTok token expired and auto-refresh failed. Channel reconnected on 2026-04-07; this post is past-due — edit and reschedule to republish.',
    updated_at = NOW()
WHERE platform = 'tiktok'
  AND status = 'publishing'
  AND last_error LIKE 'Token expired for tiktok%'
"""


async def main():
    apply = "--apply" in sys.argv
    url = os.environ.get("AMPLIFY_DB_URL")
    if not url:
        print("ERROR: set AMPLIFY_DB_URL env var", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(url, ssl="require", timeout=30)
    try:
        rows = await conn.fetch(SELECT_SQL)
        print(f"Found {len(rows)} stuck TikTok posts to mark as failed:")
        for r in rows:
            print(f"  id={str(r['id'])[:8]} sched={r['scheduled_at']} "
                  f"retries={r['retry_count']}  {r['content']!r}")

        if not rows:
            print("Nothing to do.")
            return

        if not apply:
            print()
            print("DRY RUN — re-run with --apply to actually update.")
            return

        result = await conn.execute(UPDATE_SQL)
        print()
        print(f"OK: {result}")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

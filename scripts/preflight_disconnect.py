"""Preflight: count what's tied to each connected channel before a disconnect/reconnect demo."""
import asyncio
import os
import sys

import asyncpg


async def main():
    url = os.environ.get("AMPLIFY_DB_URL")
    if not url:
        print("ERROR: set AMPLIFY_DB_URL env var", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(url, ssl="require", timeout=30)
    try:
        channels = await conn.fetch(
            "SELECT id, platform, display_name, tenant_id "
            "FROM channel_connections ORDER BY platform"
        )
        for ch in channels:
            print("=" * 70)
            print(f"{ch['platform'].upper()}  {ch['display_name']}")
            print(f"  channel_id: {ch['id']}")
            print(f"  tenant_id : {ch['tenant_id']}")

            # Posts on this channel by status
            rows = await conn.fetch(
                "SELECT status, COUNT(*) AS n FROM posts "
                "WHERE channel_id = $1 GROUP BY status ORDER BY status",
                ch['id'],
            )
            print(f"  posts on this channel:")
            total = 0
            for r in rows:
                print(f"    {r['status']:12s} {r['n']}")
                total += r['n']
            print(f"    {'TOTAL':12s} {total}")

            # Assisted tasks (this is the FK risk for disconnect)
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM assisted_tasks WHERE channel_id = $1",
                ch['id'],
            )
            tasks = row['n']
            flag = "  ⚠ WILL BLOCK DISCONNECT (FK violation)" if tasks > 0 else "  ✓"
            print(f"  assisted_tasks: {tasks}{flag}")

            # Already-orphaned posts on this platform (would re-associate to this channel on reconnect)
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM posts "
                "WHERE channel_id IS NULL AND platform = $1 AND tenant_id = $2",
                ch['platform'], ch['tenant_id'],
            )
            print(f"  already-orphaned posts on platform={ch['platform']}: {row['n']}")
            print()

        # Sanity: any posts already orphaned across all platforms?
        print("=" * 70)
        print("ALL currently orphaned posts (channel_id IS NULL):")
        rows = await conn.fetch(
            "SELECT platform, COUNT(*) AS n FROM posts "
            "WHERE channel_id IS NULL GROUP BY platform ORDER BY platform"
        )
        if not rows:
            print("  (none)")
        for r in rows:
            print(f"  {r['platform']:12s} {r['n']}")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

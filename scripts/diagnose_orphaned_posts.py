"""Check TikTok channel connection health + Redis dedup keys."""
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
        print("=" * 70)
        print("channel_connections columns:")
        print("=" * 70)
        rows = await conn.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'channel_connections' ORDER BY ordinal_position"
        )
        for r in rows:
            print(f"  {r['column_name']:30s} {r['data_type']}")

        print()
        print("=" * 70)
        print("TikTok channel row (all fields):")
        print("=" * 70)
        row = await conn.fetchrow(
            "SELECT * FROM channel_connections WHERE platform='tiktok'"
        )
        if row:
            for k in row.keys():
                v = row[k]
                if k in ("access_token", "refresh_token") and v:
                    v = f"<{len(str(v))} chars>"
                print(f"  {k:30s} {v}")
        else:
            print("  (NO TikTok channel found!)")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

"""Snapshot all channel_connections + posts to a JSON file before risky ops.

Use before disconnect/reconnect demos. To restore: see restore_channels_and_posts.py
(not yet written — DM if you actually need it; the snapshot is the important part).

Run: python scripts/snapshot_channels_and_posts.py [output_path]
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import asyncpg


def _serialize(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "hex"):  # uuid
        return str(v)
    return v


async def main():
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        f"snapshots/snapshot_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    url = os.environ.get("AMPLIFY_DB_URL")
    if not url:
        print("ERROR: set AMPLIFY_DB_URL env var", file=sys.stderr)
        sys.exit(1)

    conn = await asyncpg.connect(url, ssl="require", timeout=30)
    try:
        channels = await conn.fetch("SELECT * FROM channel_connections")
        posts = await conn.fetch("SELECT * FROM posts")
        tasks = await conn.fetch("SELECT * FROM assisted_tasks")

        snap = {
            "snapshot_at": datetime.utcnow().isoformat(),
            "counts": {
                "channels": len(channels),
                "posts": len(posts),
                "assisted_tasks": len(tasks),
            },
            "channels": [
                {k: _serialize(r[k]) for k in r.keys()} for r in channels
            ],
            "posts": [
                {k: _serialize(r[k]) for k in r.keys()} for r in posts
            ],
            "assisted_tasks": [
                {k: _serialize(r[k]) for k in r.keys()} for r in tasks
            ],
        }

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(snap, f, indent=2, default=str)

        print(f"Wrote snapshot to {out_path}")
        print(f"  channels      : {snap['counts']['channels']}")
        print(f"  posts         : {snap['counts']['posts']}")
        print(f"  assisted_tasks: {snap['counts']['assisted_tasks']}")
        print()
        print(f"File size: {out_path.stat().st_size / 1024:.1f} KB")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())

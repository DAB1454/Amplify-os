"""CLI commands for learning pipeline operations.

Usage:

    # Backfill a single tenant
    python -m worker.app.cli backfill --tenant-id <uuid>

    # Backfill all tenants
    python -m worker.app.cli backfill --all

    # Dry run (report only, no writes)
    python -m worker.app.cli backfill --tenant-id <uuid> --dry-run

    # Resume from cursor
    python -m worker.app.cli backfill --tenant-id <uuid> --cursor <post-uuid>

    # Skip specific phases
    python -m worker.app.cli backfill --tenant-id <uuid> --skip-features --skip-events

    # Custom batch size
    python -m worker.app.cli backfill --tenant-id <uuid> --batch-size 500

    # Full backfill (all batches until complete)
    python -m worker.app.cli backfill --tenant-id <uuid> --full
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def _get_all_tenant_ids(db_url: str) -> list[uuid.UUID]:
    """Load all tenant IDs from the database."""
    from amplify.db.session import get_async_session
    from amplify.db.models.tenant import TenantModel
    from sqlalchemy import select

    async with get_async_session(db_url) as db:
        result = await db.execute(select(TenantModel.id))
        return list(result.scalars().all())


async def _run_backfill(
    db_url: str,
    tenant_id: uuid.UUID,
    *,
    batch_size: int = 200,
    skip_features: bool = False,
    skip_outcomes: bool = False,
    skip_events: bool = False,
    bootstrap_patterns: bool = True,
    cursor: str | None = None,
    dry_run: bool = False,
    full: bool = False,
) -> dict:
    """Run backfill for a single tenant, optionally looping until complete."""
    from amplify.db.session import get_async_session
    from amplify.learning.backfill import BackfillEngine, BackfillReport

    reports: list[dict] = []
    current_cursor = cursor
    batch_num = 0

    while True:
        batch_num += 1
        print(f"  Batch {batch_num} (cursor={current_cursor or 'start'})...")

        async with get_async_session(db_url) as db:
            engine = BackfillEngine(db, tenant_id, dry_run=dry_run)
            report = await engine.run(
                batch_size=batch_size,
                skip_features=skip_features,
                skip_outcomes=skip_outcomes,
                skip_events=skip_events,
                bootstrap_patterns=bootstrap_patterns,
                cursor=current_cursor,
            )

        reports.append(report.to_dict())
        _print_batch_summary(report, batch_num)

        if report.is_complete or not full:
            break

        current_cursor = report.cursor

    # Aggregate if multiple batches
    if len(reports) > 1:
        return _aggregate_reports(reports, str(tenant_id))
    return reports[0]


def _print_batch_summary(report, batch_num: int) -> None:
    """Print a one-line batch summary."""
    parts = []
    if report.features_created:
        parts.append(f"features={report.features_created}")
    if report.outcomes_created:
        parts.append(f"outcomes={report.outcomes_created}")
    if report.events_created:
        parts.append(f"events={report.events_created}")
    if report.patterns_created:
        parts.append(f"patterns={report.patterns_created}")
    total_errors = report.features_errors + report.outcomes_errors + report.events_errors
    if total_errors:
        parts.append(f"errors={total_errors}")
    summary = ", ".join(parts) if parts else "no changes"
    status = "DONE" if report.is_complete else "more"
    print(f"    [{status}] {report.posts_scanned} posts — {summary}")


def _aggregate_reports(reports: list[dict], tenant_id: str) -> dict:
    """Merge multiple batch reports into one summary."""
    agg = {
        "tenant_id": tenant_id,
        "status": "completed",
        "batches": len(reports),
        "posts_scanned": sum(r["posts_scanned"] for r in reports),
        "features_created": sum(r["features_created"] for r in reports),
        "features_skipped": sum(r["features_skipped"] for r in reports),
        "features_errors": sum(r["features_errors"] for r in reports),
        "outcomes_created": sum(r["outcomes_created"] for r in reports),
        "outcomes_skipped": sum(r["outcomes_skipped"] for r in reports),
        "outcomes_errors": sum(r["outcomes_errors"] for r in reports),
        "events_created": sum(r["events_created"] for r in reports),
        "events_skipped": sum(r["events_skipped"] for r in reports),
        "events_errors": sum(r["events_errors"] for r in reports),
        "patterns_created": reports[-1].get("patterns_created", 0),
        "is_complete": reports[-1].get("is_complete", False),
        "cursor": reports[-1].get("cursor"),
        "warnings": [],
        "errors": [],
    }
    for r in reports:
        agg["warnings"].extend(r.get("warnings", []))
        agg["errors"].extend(r.get("errors", []))
    # Deduplicate warnings
    agg["warnings"] = list(dict.fromkeys(agg["warnings"]))
    return agg


def _print_report(report: dict) -> None:
    """Pretty-print a backfill report."""
    print("\n" + "=" * 60)
    print(f"BACKFILL REPORT — tenant {report.get('tenant_id', '?')}")
    print("=" * 60)
    print(f"Status:  {report.get('status', '?')}")
    if report.get("batches"):
        print(f"Batches: {report['batches']}")
    print(f"Posts:   {report.get('posts_scanned', 0)} scanned")
    print()

    print("Features:")
    print(f"  Created:  {report.get('features_created', 0)}")
    print(f"  Skipped:  {report.get('features_skipped', 0)}")
    print(f"  Errors:   {report.get('features_errors', 0)}")
    print()

    print("Outcomes:")
    print(f"  Created:  {report.get('outcomes_created', 0)}")
    print(f"  Skipped:  {report.get('outcomes_skipped', 0)}")
    print(f"  Errors:   {report.get('outcomes_errors', 0)}")
    print()

    print("Events:")
    print(f"  Created:  {report.get('events_created', 0)}")
    print(f"  Skipped:  {report.get('events_skipped', 0)}")
    print(f"  Errors:   {report.get('events_errors', 0)}")
    print()

    print(f"Patterns: {report.get('patterns_created', 0)} created")
    print(f"Complete: {report.get('is_complete', False)}")

    if report.get("cursor"):
        print(f"Cursor:  {report['cursor']}")

    warnings = report.get("warnings", [])
    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for w in warnings:
            print(f"  - {w}")

    errors = report.get("errors", [])
    if errors:
        print(f"\nErrors ({len(errors)}):")
        for e in errors[:20]:
            print(f"  - [{e.get('stage', '?')}] {e.get('post_id', '?')}: {e.get('error', '?')}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more")

    print("=" * 60)


async def cmd_backfill(args: argparse.Namespace) -> None:
    """Execute the backfill command."""
    from worker.app.config import Settings

    settings = Settings()

    if args.all:
        tenant_ids = await _get_all_tenant_ids(settings.database_url)
        if not tenant_ids:
            print("No tenants found.")
            return
        print(f"Backfilling {len(tenant_ids)} tenants...")
    else:
        tenant_ids = [uuid.UUID(args.tenant_id)]

    all_reports = []
    for tid in tenant_ids:
        print(f"\nTenant: {tid}")
        report = await _run_backfill(
            settings.database_url,
            tid,
            batch_size=args.batch_size,
            skip_features=args.skip_features,
            skip_outcomes=args.skip_outcomes,
            skip_events=args.skip_events,
            bootstrap_patterns=not args.skip_patterns,
            cursor=args.cursor,
            dry_run=args.dry_run,
            full=args.full,
        )
        _print_report(report)
        all_reports.append(report)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(all_reports, f, indent=2, default=str)
        print(f"\nReport written to {args.output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="amplify-learning",
        description="Amplify-OS learning pipeline CLI",
    )
    sub = parser.add_subparsers(dest="command")

    bf = sub.add_parser("backfill", help="Backfill learning data from historical posts")
    group = bf.add_mutually_exclusive_group(required=True)
    group.add_argument("--tenant-id", help="Single tenant UUID to backfill")
    group.add_argument("--all", action="store_true", help="Backfill all tenants")

    bf.add_argument("--batch-size", type=int, default=200, help="Posts per batch (default 200)")
    bf.add_argument("--cursor", help="Resume from this post UUID")
    bf.add_argument("--full", action="store_true", help="Loop until all posts are processed")
    bf.add_argument("--dry-run", action="store_true", help="Report only, no writes")
    bf.add_argument("--skip-features", action="store_true", help="Skip feature extraction")
    bf.add_argument("--skip-outcomes", action="store_true", help="Skip outcome computation")
    bf.add_argument("--skip-events", action="store_true", help="Skip learning event emission")
    bf.add_argument("--skip-patterns", action="store_true", help="Skip pattern bootstrap")
    bf.add_argument("--output", "-o", help="Write JSON report to file")

    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = build_parser()
    args = parser.parse_args()

    if args.command == "backfill":
        asyncio.run(cmd_backfill(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

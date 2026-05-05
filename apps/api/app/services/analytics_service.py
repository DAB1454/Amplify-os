"""Analytics service — queries, scoring, and analyst reports.

Returns honest empty states when the DB has no metrics yet. The
old silent mock fallback was removed: if a user publishes zero posts
we want them to see "no data yet", not fabricated numbers that make
the dashboard look like it's working. Mock data is still available
via the explicit ``/api/v1/analytics/demo`` route for local dev.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.core.analytics.analyst import weekly_analyst_report
from amplify.core.analytics.scoring import PostScore, score_posts
from amplify.db.models.campaign import CampaignModel
from amplify.db.models.metric import DailyMetricModel
from amplify.db.models.post import PostModel


class AnalyticsService:
    """Service layer for analytics queries."""

    def __init__(self, db: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id

    # ── overview ──────────────────────────────────────────────────

    async def get_overview(self, days: int | None = None) -> dict:
        """High-level analytics overview for the tenant.

        If ``days`` is provided, post counts and engagement totals are
        scoped to that window (created_at for posts, published_at for
        published/engagement). When ``days`` is None, returns all-time
        totals (legacy behavior).
        """
        cutoff = datetime.utcnow() - timedelta(days=days) if days else None

        campaigns = await self.db.execute(
            select(func.count()).select_from(CampaignModel).where(
                CampaignModel.tenant_id == self.tenant_id
            )
        )

        posts_q = select(func.count()).select_from(PostModel).where(
            PostModel.tenant_id == self.tenant_id,
        )
        if cutoff is not None:
            posts_q = posts_q.where(PostModel.created_at >= cutoff)
        posts = await self.db.execute(posts_q)

        pub_count_q = select(func.count()).select_from(PostModel).where(
            PostModel.tenant_id == self.tenant_id,
            PostModel.status == "published",
        )
        if cutoff is not None:
            pub_count_q = pub_count_q.where(PostModel.published_at >= cutoff)
        published = await self.db.execute(pub_count_q)

        # Aggregate total views/likes/comments from published posts' engagement JSON
        pub_engagement_q = select(PostModel.engagement, PostModel.platform).where(
            PostModel.tenant_id == self.tenant_id,
            PostModel.status == "published",
        )
        if cutoff is not None:
            pub_engagement_q = pub_engagement_q.where(PostModel.published_at >= cutoff)
        pub_posts = await self.db.execute(pub_engagement_q)
        total_views = 0
        total_likes = 0
        total_comments = 0
        total_shares = 0
        for row in pub_posts.all():
            engagement = row[0]
            if not engagement or not isinstance(engagement, dict):
                continue
            total_views += int(engagement.get("views", 0) or engagement.get("impressions", 0) or 0)
            total_likes += int(engagement.get("likes", 0) or 0)
            total_comments += int(engagement.get("comments", 0) or 0)
            total_shares += int(engagement.get("shares", 0) or 0)

        return {
            "tenant_id": str(self.tenant_id),
            "window_days": days,
            "total_campaigns": campaigns.scalar_one(),
            "total_posts": posts.scalar_one(),
            "published_posts": published.scalar_one(),
            "total_views": total_views,
            "total_likes": total_likes,
            "total_comments": total_comments,
            "total_shares": total_shares,
        }

    # ── campaign time-series ──────────────────────────────────────

    # Metrics that should be merged into "views"
    _VIEW_METRICS = {"impressions", "views", "reach"}

    async def get_campaign_timeseries(
        self,
        campaign_id: uuid.UUID | None = None,
        days: int = 30,
        platform: str | None = None,
    ) -> dict:
        """Daily views and engagement deltas for a campaign.

        Platform APIs report cumulative totals, so we compute daily deltas
        (today's value minus yesterday's) to show actual daily activity.
        Impressions, views, and reach are consolidated into a single "Views"
        metric since they measure the same thing across platforms.
        """
        # Fetch one extra day so we can compute a delta for the first visible day
        start = date.today() - timedelta(days=days + 1)
        stmt = (
            select(
                DailyMetricModel.entity_id,
                DailyMetricModel.date,
                DailyMetricModel.metric_name,
                DailyMetricModel.value,
            )
            .where(
                DailyMetricModel.tenant_id == self.tenant_id,
                DailyMetricModel.entity_type == "post",
                DailyMetricModel.date >= start,
            )
            .order_by(DailyMetricModel.entity_id, DailyMetricModel.metric_name, DailyMetricModel.date)
        )
        if platform:
            stmt = stmt.where(DailyMetricModel.source == platform)
        rows = (await self.db.execute(stmt)).all()

        # Group by (entity_id, canonical_metric) → sorted list of (date, value)
        from collections import defaultdict
        per_post: dict[tuple, list[tuple]] = defaultdict(list)
        for entity_id, row_date, metric_name, value in rows:
            canonical = "views" if metric_name in self._VIEW_METRICS else metric_name
            per_post[(entity_id, canonical)].append((row_date, float(value)))

        # Compute daily deltas, then aggregate across posts
        visible_start = date.today() - timedelta(days=days)
        daily_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for (entity_id, metric), points in per_post.items():
            points.sort(key=lambda p: p[0])
            # For view metrics, take the max value per day (they overlap)
            if metric == "views":
                by_date: dict = {}
                for d, v in points:
                    by_date[d] = max(by_date.get(d, 0), v)
                sorted_dates = sorted(by_date.keys())
                for i, d in enumerate(sorted_dates):
                    if d < visible_start:
                        continue
                    prev = by_date[sorted_dates[i - 1]] if i > 0 else 0
                    delta = max(0, by_date[d] - prev)
                    daily_totals[metric][d.isoformat()] += delta
            else:
                # Engagement is already a delta-like value (sum of interactions)
                # but still stored as cumulative, so compute delta
                prev_val = 0.0
                for d, v in points:
                    if d < visible_start:
                        prev_val = v
                        continue
                    delta = max(0, v - prev_val)
                    daily_totals[metric][d.isoformat()] += delta
                    prev_val = v

        # Build ordered series
        series: dict[str, list[dict]] = {}
        for metric in ["views", "engagement"]:
            if metric not in daily_totals:
                continue
            dates_vals = daily_totals[metric]
            series[metric] = [
                {"date": d, "value": dates_vals.get(d, 0)}
                for d in sorted(dates_vals.keys())
            ]

        # Compute real cumulative totals from PostModel.engagement so
        # the chart headline matches the overview cards.  DailyMetric
        # deltas only capture increments since we started tracking,
        # which under-counts posts imported with existing engagement.
        # No date filter here — these are lifetime totals matching the
        # overview; the sparkline shape shows the daily trend.
        post_filter = [
            PostModel.tenant_id == self.tenant_id,
            PostModel.status == "published",
        ]
        if platform:
            post_filter.append(PostModel.platform == platform)
        if campaign_id:
            post_filter.append(PostModel.campaign_id == campaign_id)
        pub_rows = await self.db.execute(
            select(PostModel.engagement).where(*post_filter)
        )
        cum_views = 0
        cum_engagement = 0
        for (engagement,) in pub_rows.all():
            if not engagement or not isinstance(engagement, dict):
                continue
            views = int(engagement.get("views", 0) or engagement.get("impressions", 0) or 0)
            cum_views += views
            cum_engagement += int(engagement.get("likes", 0) or 0) + int(engagement.get("comments", 0) or 0) + int(engagement.get("shares", 0) or 0)

        return {
            "campaign_id": str(campaign_id) if campaign_id else "all",
            "days": days,
            "series": series,
            "totals": {"views": cum_views, "engagement": cum_engagement},
            "source": "database" if series else "empty",
        }

    # ── post scores ──────────────────────────────────────────────

    async def get_post_scores(
        self,
        campaign_id: uuid.UUID | None = None,
        days: int = 14,
    ) -> list[dict]:
        """Score all published posts by normalized engagement and CTR.

        Falls back to mock data when DB is empty.
        """
        # Try to get real aggregated metrics from DB
        start = date.today() - timedelta(days=days)
        post_filter = [
            PostModel.tenant_id == self.tenant_id,
            PostModel.status == "published",
            # Restrict to the requested window. Without this, every range
            # button (7d/14d/30d) returned the same dataset because the
            # date filter was computed but never applied to the query.
            PostModel.published_at >= start,
        ]
        if campaign_id:
            post_filter.append(PostModel.campaign_id == campaign_id)

        stmt = select(
            PostModel.id, PostModel.platform, PostModel.engagement,
            PostModel.content_text, PostModel.permalink, PostModel.platform_post_id,
        ).where(*post_filter)
        rows = (await self.db.execute(stmt)).all()

        # Build a lookup so we can attach human-readable metadata after scoring.
        post_meta: dict[str, dict] = {}
        posts_data = []
        for post_id, platform, engagement, content_text, permalink, platform_post_id in rows:
            if not engagement:
                continue
            # Normalize platform-specific metric names into a common shape.
            # YouTube reports `views` instead of `impressions`; TikTok
            # likewise often only carries `views`. Treat views as the
            # reach denominator so those posts actually get scored.
            impressions = (
                engagement.get("impressions", 0)
                or engagement.get("views", 0)
                or engagement.get("reach", 0)
                or 0
            )
            likes = engagement.get("likes", 0) or 0
            if impressions <= 0 and likes <= 0 and not any(
                engagement.get(k, 0) for k in ("comments", "shares", "saves")
            ):
                continue
            pid = str(post_id)
            # Caption preview: first 80 chars, single line
            caption = (content_text or "").replace("\n", " ").strip()
            if len(caption) > 80:
                caption = caption[:77] + "..."
            post_meta[pid] = {
                "caption": caption or "(no caption)",
                "permalink": permalink or "",
                "platform_post_id": platform_post_id or "",
            }
            posts_data.append({
                "post_id": pid,
                "platform": platform,
                "impressions": impressions,
                "likes": likes,
                "comments": engagement.get("comments", 0) or 0,
                "shares": engagement.get("shares", 0) or 0,
                "saves": engagement.get("saves", 0) or 0,
                "clicks": engagement.get("clicks", 0) or 0,
            })

        if not posts_data:
            # Honest empty state. The previous behavior silently seeded
            # the dashboard with generate_demo_dataset() which made an
            # empty tenant look like it had traffic — removed in #23.
            return []

        scores = score_posts(posts_data)
        return [
            {
                "post_id": s.post_id,
                "platform": s.platform,
                "impressions": s.impressions,
                "engagement_rate": round(s.engagement_rate, 4),
                "click_through_rate": round(s.click_through_rate, 4),
                "engagement_score": s.engagement_score,
                "click_score": s.click_score,
                "composite_score": s.composite_score,
                **post_meta.get(s.post_id, {}),
            }
            for s in scores
        ]

    # ── analyst report ────────────────────────────────────────────

    async def generate_analyst_report(
        self,
        campaign_id: uuid.UUID | None = None,
        days: int = 7,
    ) -> dict:
        """Generate a weekly keep/remix/stop analyst report.

        Returns a zero-verdict report when no published posts in window.
        """
        end = date.today()
        start = end - timedelta(days=days)

        # Get scored posts
        score_dicts = await self.get_post_scores(campaign_id=campaign_id, days=days)

        cid = str(campaign_id) if campaign_id else "all"

        # Honest empty state: no published posts with engagement means
        # there's nothing for the analyst to reason about. Return a
        # zero-verdict report with a user-facing explanation instead
        # of running the analyst on fabricated mock data.
        if not score_dicts:
            return {
                "campaign_id": cid,
                "period_start": start.isoformat(),
                "period_end": end.isoformat(),
                "total_posts": 0,
                "keep_count": 0,
                "remix_count": 0,
                "stop_count": 0,
                "summary": (
                    f"No published posts with engagement in the last {days} day(s). "
                    "Publish some content and wait for metrics to land to see "
                    "keep/remix/stop verdicts here."
                ),
                "generated_at": datetime.utcnow().isoformat(),
                "verdicts": [],
            }

        # Convert to PostScore objects for the analyst
        scores = [
            PostScore(
                post_id=d["post_id"],
                platform=d["platform"],
                impressions=d["impressions"],
                engagement_rate=d["engagement_rate"],
                click_through_rate=d["click_through_rate"],
                engagement_score=d["engagement_score"],
                click_score=d["click_score"],
                composite_score=d["composite_score"],
            )
            for d in score_dicts
        ]

        # Build a lookup to attach caption/permalink to each verdict
        meta_lookup = {
            d["post_id"]: d for d in score_dicts
        }

        report = weekly_analyst_report(
            campaign_id=cid,
            period_start=start.isoformat(),
            period_end=end.isoformat(),
            scores=scores,
        )

        return {
            "campaign_id": report.campaign_id,
            "period_start": report.period_start,
            "period_end": report.period_end,
            "total_posts": report.total_posts,
            "keep_count": report.keep_count,
            "remix_count": report.remix_count,
            "stop_count": report.stop_count,
            "summary": report.summary,
            "generated_at": report.generated_at.isoformat(),
            "verdicts": [
                {
                    "post_id": v.post_id,
                    "platform": v.platform,
                    "composite_score": v.composite_score,
                    "engagement_score": v.engagement_score,
                    "click_score": v.click_score,
                    "verdict": v.verdict.value,
                    "reason": v.reason,
                    # Attach human-readable metadata from the scores query
                    **{
                        k: meta_lookup.get(v.post_id, {}).get(k, "")
                        for k in ("caption", "permalink", "platform_post_id")
                    },
                }
                for v in report.verdicts
            ],
        }

    # ── experiment results ────────────────────────────────────────

    async def get_experiment_results(self, experiment_id: uuid.UUID) -> dict:
        """Get scoring results for experiment variants."""
        from amplify.db.models.experiment import ExperimentModel

        stmt = select(ExperimentModel).where(
            ExperimentModel.id == experiment_id,
            ExperimentModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        experiment = result.scalar_one_or_none()
        if experiment is None:
            raise ValueError(f"Experiment {experiment_id} not found")

        variants = experiment.variants or []
        outcome = experiment.outcome or {}

        return {
            "experiment_id": str(experiment_id),
            "name": experiment.name,
            "hypothesis": experiment.hypothesis,
            "status": experiment.status,
            "variants": variants,
            "outcome": outcome,
            "winner_variant": experiment.winner_variant,
        }


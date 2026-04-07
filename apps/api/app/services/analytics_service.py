"""Analytics service — queries, scoring, and analyst reports.

Falls back to mock data when the DB has no metrics, so the analyst
flow can be demonstrated without live API integrations.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.core.analytics.analyst import AnalystRecommendation, weekly_analyst_report
from amplify.core.analytics.mock_data import generate_campaign_metrics, generate_demo_dataset
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

    async def get_overview(self) -> dict:
        """High-level analytics overview for the tenant."""
        campaigns = await self.db.execute(
            select(func.count()).select_from(CampaignModel).where(
                CampaignModel.tenant_id == self.tenant_id
            )
        )
        posts = await self.db.execute(
            select(func.count()).select_from(PostModel).where(
                PostModel.tenant_id == self.tenant_id
            )
        )
        published = await self.db.execute(
            select(func.count()).select_from(PostModel).where(
                PostModel.tenant_id == self.tenant_id,
                PostModel.status == "published",
            )
        )

        return {
            "tenant_id": str(self.tenant_id),
            "total_campaigns": campaigns.scalar_one(),
            "total_posts": posts.scalar_one(),
            "published_posts": published.scalar_one(),
        }

    # ── campaign time-series ──────────────────────────────────────

    async def get_campaign_timeseries(
        self,
        campaign_id: uuid.UUID | None = None,
        days: int = 30,
    ) -> dict:
        """Daily impressions, engagement, clicks for a campaign.

        Falls back to mock data if no DailyMetric rows exist.
        """
        # Try real data first
        start = date.today() - timedelta(days=days)
        stmt = (
            select(
                DailyMetricModel.date,
                DailyMetricModel.metric_name,
                func.sum(DailyMetricModel.value),
            )
            .where(
                DailyMetricModel.tenant_id == self.tenant_id,
                DailyMetricModel.entity_type == "post",
                DailyMetricModel.date >= start,
            )
            .group_by(DailyMetricModel.date, DailyMetricModel.metric_name)
            .order_by(DailyMetricModel.date)
        )
        rows = (await self.db.execute(stmt)).all()

        if rows:
            series: dict[str, list[dict]] = {}
            for row_date, metric_name, total in rows:
                series.setdefault(metric_name, []).append({
                    "date": row_date.isoformat(),
                    "value": float(total),
                })
            return {
                "campaign_id": str(campaign_id) if campaign_id else "all",
                "days": days,
                "series": series,
                "source": "database",
            }

        # Fall back to mock data
        return self._mock_campaign_timeseries(str(campaign_id) if campaign_id else "all", days)

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

        stmt = select(PostModel.id, PostModel.platform, PostModel.engagement).where(*post_filter)
        rows = (await self.db.execute(stmt)).all()

        posts_data = []
        for post_id, platform, engagement in rows:
            if engagement and any(engagement.get(k, 0) > 0 for k in ("impressions", "likes")):
                posts_data.append({
                    "post_id": str(post_id),
                    "platform": platform,
                    **{k: engagement.get(k, 0) for k in
                       ("impressions", "likes", "comments", "shares", "saves", "clicks")},
                })

        if not posts_data:
            # Fall back to mock
            demo = generate_demo_dataset()
            posts_data = list(demo["aggregated"].values())

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

        Falls back to mock data when DB is empty.
        """
        end = date.today()
        start = end - timedelta(days=days)

        # Get scored posts
        score_dicts = await self.get_post_scores(campaign_id=campaign_id, days=days)

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

        cid = str(campaign_id) if campaign_id else "all"
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

    # ── internal helpers ──────────────────────────────────────────

    @staticmethod
    def _mock_campaign_timeseries(campaign_id: str, days: int) -> dict:
        """Generate mock time-series data for demo purposes."""
        demo = generate_demo_dataset()
        daily = demo["daily_metrics"]

        # Aggregate by date
        by_date: dict[str, dict[str, int]] = {}
        for m in daily:
            d = m["date"]
            if d not in by_date:
                by_date[d] = {"impressions": 0, "engagement": 0, "clicks": 0}
            by_date[d]["impressions"] += m["impressions"]
            by_date[d]["engagement"] += m["likes"] + m["comments"] + m["shares"] + m["saves"]
            by_date[d]["clicks"] += m["clicks"]

        series = {
            "impressions": [{"date": d, "value": v["impressions"]} for d, v in sorted(by_date.items())],
            "engagement": [{"date": d, "value": v["engagement"]} for d, v in sorted(by_date.items())],
            "clicks": [{"date": d, "value": v["clicks"]} for d, v in sorted(by_date.items())],
        }

        return {
            "campaign_id": campaign_id,
            "days": days,
            "series": series,
            "source": "mock",
        }

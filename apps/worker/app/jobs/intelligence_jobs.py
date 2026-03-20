"""Scheduled intelligence pipeline jobs.

These run on a periodic schedule via the worker's Scheduler:
- feature_extraction: Extract features from recent posts
- reward_computation: Compute rewards for posts with fresh outcomes
- tenant_pattern_update: Re-learn tenant patterns from accumulated data
- cohort_aggregation: Recompute cohort stats and global priors
- evaluation_run: Run offline replay evaluation
- model_health_check: Verify pipeline health and emit alerts

Each job is also available as a queue handler for on-demand invocation.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone, timedelta

from amplify.observability.metrics import MetricsCollector
from amplify.db.session import get_async_session

logger = logging.getLogger(__name__)
metrics = MetricsCollector()


# ── Feature Extraction ───────────────────────────────────────────


async def extract_features(payload: dict) -> dict:
    """Extract feature vectors from posts published since last run.

    Runs nightly. Finds posts without feature vectors and extracts them.
    """
    from sqlalchemy import select, func
    from amplify.db.models.post import PostModel
    from amplify.db.models.learning import PostFeatureVectorModel
    from app.config import Settings

    settings = Settings()
    tenant_id = payload.get("tenant_id")
    hours_back = payload.get("hours_back", 25)  # slightly > 24h for overlap
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)

    async with get_async_session(settings.database_url) as db:
        # Find published posts without feature vectors
        stmt = (
            select(PostModel)
            .outerjoin(
                PostFeatureVectorModel,
                PostModel.id == PostFeatureVectorModel.post_id,
            )
            .where(
                PostFeatureVectorModel.id.is_(None),
                PostModel.status == "published",
                PostModel.published_at >= cutoff,
            )
        )
        if tenant_id:
            stmt = stmt.where(PostModel.tenant_id == uuid.UUID(tenant_id))

        stmt = stmt.limit(500)  # batch cap

        result = await db.execute(stmt)
        posts = list(result.scalars().all())

        extracted = 0
        errors = 0
        for post in posts:
            try:
                from amplify.learning.feature_extraction.extractor import FeatureExtractor
                extractor = FeatureExtractor()
                features = extractor.extract(post)

                fv = PostFeatureVectorModel(
                    id=uuid.uuid4(),
                    tenant_id=post.tenant_id,
                    post_id=post.id,
                    artist_id=getattr(post, "artist_id", None),
                    campaign_id=getattr(post, "campaign_id", None),
                    features=features,
                    platform=getattr(post, "platform", None),
                    content_type=features.get("content_type"),
                    post_hour=features.get("post_hour"),
                    post_day_of_week=features.get("post_day_of_week"),
                    caption_length=features.get("caption_length"),
                    hashtag_count=features.get("hashtag_count"),
                    has_link=features.get("has_link"),
                    campaign_phase=features.get("campaign_phase"),
                    extracted_at=datetime.now(timezone.utc),
                )
                db.add(fv)
                extracted += 1
            except Exception:
                logger.exception("Feature extraction failed for post %s", post.id)
                errors += 1

        if extracted:
            await db.commit()

    metrics.gauge("intelligence.features.extracted", extracted)
    metrics.gauge("intelligence.features.errors", errors)
    metrics.gauge("intelligence.features.pending", len(posts))
    logger.info(
        "Feature extraction: %d extracted, %d errors", extracted, errors,
    )
    return {"extracted": extracted, "errors": errors}


# ── Reward Computation ───────────────────────────────────────────


async def compute_rewards(payload: dict) -> dict:
    """Compute reward scores for outcomes missing rewards.

    Runs every few hours to process fresh metric windows.
    """
    from sqlalchemy import select
    from amplify.db.models.learning import PostOutcomeModel
    from app.config import Settings

    settings = Settings()
    tenant_id = payload.get("tenant_id")

    async with get_async_session(settings.database_url) as db:
        stmt = (
            select(PostOutcomeModel)
            .where(PostOutcomeModel.reward.is_(None))
            .limit(500)
        )
        if tenant_id:
            stmt = stmt.where(PostOutcomeModel.tenant_id == uuid.UUID(tenant_id))

        result = await db.execute(stmt)
        outcomes = list(result.scalars().all())

        computed = 0
        for outcome in outcomes:
            try:
                from amplify.learning.rewards.calculator import RewardCalculator
                calc = RewardCalculator()
                reward, breakdown = calc.compute(outcome)
                outcome.reward = reward
                outcome.reward_breakdown = breakdown
                computed += 1
            except Exception:
                logger.exception("Reward computation failed for outcome %s", outcome.id)

        if computed:
            await db.commit()

    metrics.gauge("intelligence.rewards.computed", computed)
    metrics.gauge("intelligence.rewards.pending", len(outcomes))
    logger.info("Reward computation: %d computed from %d outcomes", computed, len(outcomes))
    return {"computed": computed, "total_pending": len(outcomes)}


# ── Tenant Pattern Update ────────────────────────────────────────


async def update_tenant_patterns(payload: dict) -> dict:
    """Re-learn tenant patterns from accumulated data.

    Runs nightly per tenant. Discovers new patterns, updates existing,
    deactivates stale ones. Respects pinned patterns.
    """
    from sqlalchemy import select, func
    from amplify.db.models.learning import PostFeatureVectorModel
    from app.config import Settings

    settings = Settings()
    specific_tenant = payload.get("tenant_id")

    async with get_async_session(settings.database_url) as db:
        if specific_tenant:
            tenant_ids = [uuid.UUID(specific_tenant)]
        else:
            # Get all active tenants with feature data
            stmt = (
                select(PostFeatureVectorModel.tenant_id)
                .group_by(PostFeatureVectorModel.tenant_id)
                .having(func.count(PostFeatureVectorModel.id) >= 5)
            )
            result = await db.execute(stmt)
            tenant_ids = [row[0] for row in result.all()]

        updated = 0
        errors = 0
        for tid in tenant_ids:
            try:
                from app.services.tenant_pattern_service import TenantPatternService
                svc = TenantPatternService(db, tid)
                patterns = await svc.update_patterns()
                updated += len(patterns)
                await db.commit()
            except Exception:
                logger.exception("Pattern update failed for tenant %s", tid)
                await db.rollback()
                errors += 1

    metrics.gauge("intelligence.patterns.updated", updated)
    metrics.gauge("intelligence.patterns.tenants_processed", len(tenant_ids))
    metrics.gauge("intelligence.patterns.errors", errors)
    logger.info(
        "Tenant pattern update: %d patterns across %d tenants, %d errors",
        updated, len(tenant_ids), errors,
    )
    return {"tenants_processed": len(tenant_ids), "patterns_updated": updated, "errors": errors}


# ── Cohort Aggregation ───────────────────────────────────────────


async def aggregate_cohorts(payload: dict) -> dict:
    """Recompute cohort statistics and global priors.

    Runs weekly or on-demand. Aggregates cross-tenant observations,
    enforces k-anonymity, and persists GlobalPatternStat records.
    """
    from sqlalchemy import select
    from amplify.db.models.learning import LearningEventModel
    from amplify.learning.schemas.observation import Observation
    from app.config import Settings

    settings = Settings()
    days_back = payload.get("days_back", 30)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    async with get_async_session(settings.database_url) as db:
        # Load recent observations from all tenants
        stmt = (
            select(LearningEventModel)
            .where(
                LearningEventModel.observed_at >= cutoff,
                LearningEventModel.reward.is_not(None),
            )
            .limit(10000)
        )
        result = await db.execute(stmt)
        events = list(result.scalars().all())

        observations = [
            Observation(
                tenant_id=e.tenant_id,
                action_type=e.action_type,
                features=e.features,
                reward=e.reward,
            )
            for e in events
        ]

        from app.services.global_prior_service import DBGlobalPriorService
        svc = DBGlobalPriorService(db)
        stored = await svc.compute_and_store(observations)
        await db.commit()

    metrics.gauge("intelligence.cohorts.observations", len(observations))
    metrics.gauge("intelligence.cohorts.priors_stored", len(stored))
    logger.info(
        "Cohort aggregation: %d observations → %d global priors",
        len(observations), len(stored),
    )
    return {"observations": len(observations), "priors_stored": len(stored)}


# ── Evaluation Run ───────────────────────────────────────────────


async def run_evaluation(payload: dict) -> dict:
    """Run offline replay evaluation for a tenant.

    Compares predicted rankings against actual outcomes to measure
    learning system accuracy.
    """
    from app.config import Settings

    settings = Settings()
    tenant_id = payload.get("tenant_id")
    if not tenant_id:
        return {"status": "error", "error": "tenant_id required"}

    async with get_async_session(settings.database_url) as db:
        from app.services.evaluation_service import EvaluationService
        from amplify.learning.ranking.post_ranker import PostRanker
        from amplify.learning.evaluation.replay import RankingPolicySpec

        svc = EvaluationService(db, uuid.UUID(tenant_id))
        policies = [
            RankingPolicySpec(
                name="default",
                ranker=PostRanker(),
                description="Default ranking policy",
            ),
        ]
        run = await svc.run_evaluation(
            policies=policies,
            evaluation_type=payload.get("evaluation_type", "ranking"),
            top_k=payload.get("top_k", 3),
        )
        await db.commit()

    metrics.histogram("intelligence.evaluation.mae", run.mae or 0, {"tenant_id": tenant_id})
    if run.rank_correlation is not None:
        metrics.histogram("intelligence.evaluation.rank_correlation", run.rank_correlation, {"tenant_id": tenant_id})
    logger.info("Evaluation run completed for tenant %s: MAE=%s", tenant_id, run.mae)
    return {
        "run_id": str(run.id),
        "mae": run.mae,
        "rank_correlation": run.rank_correlation,
        "sample_size": run.sample_size,
    }


# ── Model Health Check ───────────────────────────────────────────


async def check_model_health(payload: dict) -> dict:
    """Check intelligence pipeline health and emit alerts.

    Checks:
    - Stale features (posts published >24h ago without feature vectors)
    - Missing reward windows (outcomes >48h old without rewards)
    - Low-confidence pattern ratio
    - Evaluation regression (latest MAE worse than previous)
    """
    from sqlalchemy import select, func
    from amplify.db.models.post import PostModel
    from amplify.db.models.learning import (
        PostFeatureVectorModel,
        PostOutcomeModel,
        TenantPatternModel,
        EvaluationRunModel,
    )
    from app.config import Settings

    settings = Settings()
    alerts: list[dict] = []

    async with get_async_session(settings.database_url) as db:
        now = datetime.now(timezone.utc)

        # 1. Stale features — published posts without feature vectors
        stale_cutoff = now - timedelta(hours=24)
        stale_count = await db.scalar(
            select(func.count(PostModel.id))
            .outerjoin(PostFeatureVectorModel, PostModel.id == PostFeatureVectorModel.post_id)
            .where(
                PostModel.status == "published",
                PostModel.published_at <= stale_cutoff,
                PostModel.published_at >= now - timedelta(days=7),
                PostFeatureVectorModel.id.is_(None),
            )
        )
        if stale_count and stale_count > 0:
            alerts.append({
                "type": "stale_features",
                "severity": "warning",
                "message": f"{stale_count} published posts missing feature vectors (>24h old)",
                "count": stale_count,
            })

        # 2. Missing reward windows
        reward_cutoff = now - timedelta(hours=48)
        missing_rewards = await db.scalar(
            select(func.count(PostOutcomeModel.id)).where(
                PostOutcomeModel.reward.is_(None),
                PostOutcomeModel.measured_at <= reward_cutoff,
            )
        )
        if missing_rewards and missing_rewards > 0:
            alerts.append({
                "type": "missing_rewards",
                "severity": "warning",
                "message": f"{missing_rewards} outcomes >48h old without computed rewards",
                "count": missing_rewards,
            })

        # 3. Low-confidence ratio per tenant
        low_conf = await db.scalar(
            select(func.count(TenantPatternModel.id)).where(
                TenantPatternModel.is_active == True,  # noqa: E712
                TenantPatternModel.confidence < 0.3,
            )
        )
        total_patterns = await db.scalar(
            select(func.count(TenantPatternModel.id)).where(
                TenantPatternModel.is_active == True,  # noqa: E712
            )
        )
        if total_patterns and total_patterns > 0:
            ratio = (low_conf or 0) / total_patterns
            if ratio > 0.5:
                alerts.append({
                    "type": "high_low_confidence_ratio",
                    "severity": "warning",
                    "message": (
                        f"{low_conf}/{total_patterns} active patterns have "
                        f"confidence < 0.3 ({ratio:.0%})"
                    ),
                    "ratio": round(ratio, 4),
                })

        # 4. Evaluation regression
        eval_stmt = (
            select(EvaluationRunModel)
            .where(EvaluationRunModel.status == "completed")
            .order_by(EvaluationRunModel.created_at.desc())
            .limit(2)
        )
        eval_result = await db.execute(eval_stmt)
        recent_runs = list(eval_result.scalars().all())

        if len(recent_runs) == 2 and recent_runs[0].mae and recent_runs[1].mae:
            current_mae = recent_runs[0].mae
            previous_mae = recent_runs[1].mae
            if current_mae > previous_mae * 1.2:  # 20% regression threshold
                alerts.append({
                    "type": "evaluation_regression",
                    "severity": "warning",
                    "message": (
                        f"Latest MAE ({current_mae:.4f}) is >{20}% worse than "
                        f"previous ({previous_mae:.4f})"
                    ),
                    "current_mae": current_mae,
                    "previous_mae": previous_mae,
                })

    status = "healthy" if not alerts else "degraded"
    metrics.gauge("intelligence.health.alert_count", len(alerts))
    metrics.gauge("intelligence.health.status", 1 if status == "healthy" else 0)
    for alert in alerts:
        metrics.increment("intelligence.health.alert", {"type": alert["type"], "severity": alert["severity"]})
    logger.info("Model health check: %s (%d alerts)", status, len(alerts))
    return {"status": status, "alerts": alerts, "checked_at": now.isoformat()}

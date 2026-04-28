"""A/B experiment loop job — evaluates variants and picks winners.

Payload schema:
{
    "tenant_id": "uuid",
    "experiment_id": "uuid"
}

Each experiment has a `variants` JSON array. Each variant is a dict with:
  - name: str (e.g. "Variant A")
  - post_ids: list[str] — UUIDs of posts assigned to this variant

The job scores each variant's posts using real engagement metrics,
compares average composite scores, and declares a winner when
confidence is high enough.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import select

logger = logging.getLogger(__name__)

# Minimum score difference (percentage points) to declare a winner
MIN_CONFIDENCE = 10.0
# Minimum posts per variant with engagement data
MIN_POSTS_PER_VARIANT = 2


async def run_experiment_loop(payload: dict) -> dict:
    """Execute an experiment iteration: score variants from real metrics, pick winner."""
    from amplify.core.analytics.scoring import score_posts
    from amplify.core.analytics.analyst import weekly_analyst_report
    from amplify.db.session import get_async_session
    from amplify.db.models.experiment import ExperimentModel
    from amplify.db.models.post import PostModel
    from app.config import Settings

    settings = Settings()
    experiment_id_str = payload.get("experiment_id")
    tenant_id_str = payload.get("tenant_id")

    if not experiment_id_str or not tenant_id_str:
        return {"status": "error", "error": "experiment_id and tenant_id required"}

    experiment_id = uuid.UUID(experiment_id_str)
    tenant_id = uuid.UUID(tenant_id_str)

    async with get_async_session(settings.database_url) as db:
        # Load experiment
        result = await db.execute(
            select(ExperimentModel).where(
                ExperimentModel.id == experiment_id,
                ExperimentModel.tenant_id == tenant_id,
            )
        )
        experiment = result.scalar_one_or_none()
        if experiment is None:
            return {"status": "error", "error": f"Experiment {experiment_id} not found"}

        if experiment.status not in ("running", "draft"):
            return {
                "status": "ok",
                "experiment_id": str(experiment_id),
                "message": f"Experiment is {experiment.status}, skipping",
            }

        variants = experiment.variants or []
        if len(variants) < 2:
            return {"status": "error", "error": "Experiment needs at least 2 variants"}

        # Collect all post IDs across variants
        all_post_ids: list[uuid.UUID] = []
        for v in variants:
            for pid in v.get("post_ids", []):
                try:
                    all_post_ids.append(uuid.UUID(pid))
                except (ValueError, TypeError):
                    pass

        if not all_post_ids:
            return {"status": "error", "error": "No post IDs assigned to variants"}

        # Query real engagement data for all posts
        stmt = select(
            PostModel.id, PostModel.platform, PostModel.engagement,
        ).where(
            PostModel.id.in_(all_post_ids),
            PostModel.tenant_id == tenant_id,
            PostModel.status == "published",
        )
        rows = (await db.execute(stmt)).all()

        # Build engagement map
        engagement_map: dict[str, dict] = {}
        for post_id, platform, engagement in rows:
            if not engagement or not isinstance(engagement, dict):
                continue
            impressions = (
                engagement.get("impressions", 0)
                or engagement.get("views", 0)
                or engagement.get("reach", 0)
                or 0
            )
            likes = engagement.get("likes", 0) or 0
            if impressions <= 0 and likes <= 0:
                continue
            engagement_map[str(post_id)] = {
                "post_id": str(post_id),
                "platform": platform or "",
                "impressions": impressions,
                "likes": likes,
                "comments": engagement.get("comments", 0) or 0,
                "shares": engagement.get("shares", 0) or 0,
                "saves": engagement.get("saves", 0) or 0,
                "clicks": engagement.get("clicks", 0) or 0,
            }

        # Score each variant's posts
        variant_results = []
        all_posts_data = []

        for i, v in enumerate(variants):
            v_post_ids = [str(pid) for pid in v.get("post_ids", [])]
            v_data = [engagement_map[pid] for pid in v_post_ids if pid in engagement_map]
            all_posts_data.extend(v_data)
            variant_results.append({
                "name": v.get("name", f"Variant {i}"),
                "post_count": len(v_post_ids),
                "posts_with_data": len(v_data),
                "post_ids": v_post_ids,
                "data": v_data,
            })

        # Score all posts together for normalized comparison
        if not all_posts_data:
            return {
                "status": "ok",
                "experiment_id": str(experiment_id),
                "message": "No published posts with engagement data yet",
                "variants": [
                    {"name": vr["name"], "avg_score": 0, "posts_with_data": 0}
                    for vr in variant_results
                ],
            }

        scores = score_posts(all_posts_data)
        score_map = {s.post_id: s for s in scores}

        # Compute per-variant averages
        for vr in variant_results:
            v_scores = [score_map[pid] for pid in vr["post_ids"] if pid in score_map]
            if v_scores:
                vr["avg_score"] = round(
                    sum(s.composite_score for s in v_scores) / len(v_scores), 1
                )
            else:
                vr["avg_score"] = 0.0
            del vr["data"]  # Don't persist raw data

        # Determine winner
        sorted_variants = sorted(enumerate(variant_results), key=lambda x: x[1]["avg_score"], reverse=True)
        best_idx, best = sorted_variants[0]
        second_best = sorted_variants[1][1] if len(sorted_variants) > 1 else None

        confidence = 0.0
        if second_best and max(best["avg_score"], 1) > 0:
            confidence = round(
                abs(best["avg_score"] - second_best["avg_score"])
                / max(best["avg_score"], second_best["avg_score"], 1)
                * 100,
                1,
            )

        # Check if we have enough data and confidence to declare a winner
        enough_data = all(
            vr["posts_with_data"] >= MIN_POSTS_PER_VARIANT for vr in variant_results
        )
        winner = best_idx if enough_data and confidence >= MIN_CONFIDENCE else None

        # Build outcome
        outcome = {
            "variants": [
                {
                    "name": vr["name"],
                    "avg_score": vr["avg_score"],
                    "post_count": vr["post_count"],
                    "posts_with_data": vr["posts_with_data"],
                }
                for vr in variant_results
            ],
            "confidence": confidence,
            "evaluated_at": datetime.utcnow().isoformat(),
        }

        # Persist results
        experiment.outcome = outcome
        if winner is not None:
            experiment.winner_variant = winner
            experiment.status = "completed"
            experiment.completed_at = datetime.utcnow()
            logger.info(
                "Experiment %s completed: %s wins (score=%.1f, confidence=%.1f%%)",
                experiment_id, variant_results[winner]["name"],
                variant_results[winner]["avg_score"], confidence,
            )
        else:
            # Mark as running if still in draft
            if experiment.status == "draft":
                experiment.status = "running"
                experiment.started_at = datetime.utcnow()
            reason = "not enough data" if not enough_data else f"confidence {confidence}% < {MIN_CONFIDENCE}%"
            logger.info(
                "Experiment %s: no winner yet (%s). Best: %s (%.1f)",
                experiment_id, reason,
                best["name"], best["avg_score"],
            )

        await db.flush()

    # Generate analyst report for the winning variant's posts
    report_data = {}
    if winner is not None and variant_results[winner].get("post_ids"):
        winner_posts = [
            engagement_map[pid]
            for pid in variant_results[winner]["post_ids"]
            if pid in engagement_map
        ]
        if winner_posts:
            winner_scores = score_posts(winner_posts)
            report = weekly_analyst_report(
                campaign_id=str(experiment_id),
                period_start=datetime.utcnow().date().isoformat(),
                period_end=datetime.utcnow().date().isoformat(),
                scores=winner_scores,
            )
            report_data = {
                "keep_count": report.keep_count,
                "remix_count": report.remix_count,
                "stop_count": report.stop_count,
                "summary": report.summary,
            }

    return {
        "status": "ok",
        "experiment_id": str(experiment_id),
        "winner": winner,
        "winner_name": variant_results[winner]["name"] if winner is not None else None,
        "confidence": confidence,
        "variants": [
            {"name": vr["name"], "avg_score": vr["avg_score"], "posts_with_data": vr["posts_with_data"]}
            for vr in variant_results
        ],
        **report_data,
    }

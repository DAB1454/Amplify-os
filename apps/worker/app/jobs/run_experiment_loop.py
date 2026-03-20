"""A/B experiment loop job — evaluates variants and picks winners.

Payload schema:
{
    "tenant_id": "uuid",
    "experiment_id": "uuid",
    "use_mock": true
}
"""

from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


async def run_experiment_loop(payload: dict) -> dict:
    """Execute an experiment iteration: score variants, pick winner.

    For each experiment in RUNNING state:
    1. Gather metrics for each variant's posts
    2. Score and compare
    3. If enough data, declare a winner and recommend keep/remix/stop
    """
    from amplify.core.analytics.mock_data import generate_demo_dataset
    from amplify.core.analytics.scoring import score_posts
    from amplify.core.analytics.analyst import weekly_analyst_report

    experiment_id = payload.get("experiment_id", "unknown")
    use_mock = payload.get("use_mock", True)

    if not use_mock:
        # TODO: load experiment from DB, get real metrics
        logger.warning("Real experiment loop not yet wired to DB")
        return {"status": "ok", "experiment_id": experiment_id, "iteration": 0, "winner": None}

    # Demo mode — use mock data to illustrate the flow
    demo = generate_demo_dataset()
    aggregated = list(demo["aggregated"].values())

    # Score all posts
    scores = score_posts(aggregated)

    # Split into two simulated variants
    mid = len(scores) // 2
    variant_a = scores[:mid]
    variant_b = scores[mid:]

    avg_a = sum(s.composite_score for s in variant_a) / len(variant_a) if variant_a else 0
    avg_b = sum(s.composite_score for s in variant_b) / len(variant_b) if variant_b else 0

    winner = 0 if avg_a >= avg_b else 1
    confidence = abs(avg_a - avg_b) / max(avg_a, avg_b, 1) * 100

    # Generate analyst report for the winning variant's posts
    report = weekly_analyst_report(
        campaign_id=demo["campaign_id"],
        period_start=(datetime.utcnow().date().isoformat()),
        period_end=(datetime.utcnow().date().isoformat()),
        scores=scores,
    )

    logger.info(
        "Experiment %s: variant %d wins (A=%.1f, B=%.1f, confidence=%.1f%%)",
        experiment_id,
        winner,
        avg_a,
        avg_b,
        confidence,
    )

    return {
        "status": "ok",
        "experiment_id": experiment_id,
        "iteration": 1,
        "winner": winner,
        "variant_a_avg": round(avg_a, 1),
        "variant_b_avg": round(avg_b, 1),
        "confidence": round(confidence, 1),
        "keep_count": report.keep_count,
        "remix_count": report.remix_count,
        "stop_count": report.stop_count,
        "summary": report.summary,
    }

"""Intelligence dashboard routes — operator visibility into learning decisions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id
from app.services.intelligence_dashboard_service import IntelligenceDashboardService

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


# ── Dependencies ─────────────────────────────────────────────────


def _get_service(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> IntelligenceDashboardService:
    return IntelligenceDashboardService(db, tenant_id)


# ── Overview ─────────────────────────────────────────────────────


@router.get(
    "/overview",
    summary="Intelligence health overview",
)
async def get_overview(
    svc: IntelligenceDashboardService = Depends(_get_service),
):
    """High-level metrics: active patterns, feature vectors, outcomes,
    events, evaluation runs, global priors, and flagged items."""
    return await svc.get_overview()


# ── Tenant Patterns ──────────────────────────────────────────────


@router.get(
    "/patterns",
    summary="List tenant patterns with evidence",
)
async def list_patterns(
    pattern_type: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    artist_id: uuid.UUID | None = Query(default=None),
    min_confidence: float | None = Query(default=None, ge=0, le=1),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    svc: IntelligenceDashboardService = Depends(_get_service),
):
    """Tenant patterns with confidence, sample size, evidence, and source labels."""
    return await svc.get_tenant_patterns(
        pattern_type=pattern_type,
        direction=direction,
        artist_id=artist_id,
        min_confidence=min_confidence,
        limit=limit,
        offset=offset,
    )


# ── Global Priors ────────────────────────────────────────────────


@router.get(
    "/global-priors",
    summary="List global priors with sample sizes",
)
async def list_global_priors(
    category: str | None = Query(default=None),
    min_tenant_count: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    svc: IntelligenceDashboardService = Depends(_get_service),
):
    """Global pattern stats with confidence intervals and tenant counts."""
    return await svc.get_global_priors(
        category=category,
        min_tenant_count=min_tenant_count,
        limit=limit,
        offset=offset,
    )


# ── Reward Trends ────────────────────────────────────────────────


@router.get(
    "/reward-trends",
    summary="Reward trend over time",
)
async def get_reward_trends(
    days: int = Query(default=30, ge=1, le=365),
    platform: str | None = Query(default=None),
    artist_id: uuid.UUID | None = Query(default=None),
    campaign_id: uuid.UUID | None = Query(default=None),
    svc: IntelligenceDashboardService = Depends(_get_service),
):
    """Average reward per day with min/max range. Filter by platform, artist, or campaign."""
    return await svc.get_reward_trends(
        days=days,
        platform=platform,
        artist_id=artist_id,
        campaign_id=campaign_id,
    )


# ── Post Drill-Down ──────────────────────────────────────────────


@router.get(
    "/posts/{post_id}",
    summary="Drill down into a post's learning signals",
)
async def get_post_intelligence(
    post_id: uuid.UUID,
    svc: IntelligenceDashboardService = Depends(_get_service),
):
    """Full view: features, outcomes, events, matching patterns, matching
    global priors, and audit trail for a single post."""
    return await svc.get_post_intelligence(post_id)


# ── Evaluation Runs ──────────────────────────────────────────────


@router.get(
    "/evaluations",
    summary="Evaluation run summaries",
)
async def get_evaluation_summary(
    limit: int = Query(default=20, ge=1, le=100),
    svc: IntelligenceDashboardService = Depends(_get_service),
):
    """Recent evaluation runs with MAE, rank correlation, and top-k precision."""
    return await svc.get_evaluation_summary(limit=limit)


# ── Prompt Performance ───────────────────────────────────────────


@router.get(
    "/prompts",
    summary="Prompt version performance",
)
async def get_prompt_performance(
    svc: IntelligenceDashboardService = Depends(_get_service),
):
    """Active prompt versions with event counts and average reward."""
    return await svc.get_prompt_performance()


# ── Ranking Decisions ────────────────────────────────────────────


@router.get(
    "/decisions",
    summary="Recent ranking and bandit decisions",
)
async def get_ranking_decisions(
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=200),
    svc: IntelligenceDashboardService = Depends(_get_service),
):
    """Audit trail of ranking, scoring, and exploration decisions with explanations."""
    return await svc.get_ranking_decisions(days=days, limit=limit)


# ── Operator Audit ───────────────────────────────────────────────


@router.get(
    "/audit",
    summary="Learning audit trail",
)
async def get_audit_trail(
    action: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    suspicious_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    svc: IntelligenceDashboardService = Depends(_get_service),
):
    """Full audit trail with optional filter for suspicious/low-confidence entries."""
    return await svc.get_audit_trail(
        action=action,
        entity_type=entity_type,
        days=days,
        suspicious_only=suspicious_only,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/alerts",
    summary="Suspicious and low-confidence intelligence alerts",
)
async def get_alerts(
    days: int = Query(default=7, ge=1, le=90),
    svc: IntelligenceDashboardService = Depends(_get_service),
):
    """Surface learning actions that need operator attention:
    low-confidence patterns, thin data, rapid changes, anomalous rewards."""
    return await svc.get_suspicious_actions(days=days)


# ── Bandit Summary ──────────────────────────────────────────────


@router.get(
    "/bandit-summary",
    summary="Contextual bandit status and arm statistics",
)
async def get_bandit_summary(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Returns bandit arm stats, exploration rate, and shadow mode status."""
    from app.services.bandit_service import load_bandit
    from amplify.learning.ranking.bandit import TenantBanditState

    bandit = await load_bandit(db, tenant_id)
    state = TenantBanditState.from_bandit(str(tenant_id), bandit)

    arms_summary = []
    for arm_id, arm_data in state.arms.items():
        arms_summary.append({
            "arm_id": arm_id,
            "n_pulls": arm_data.get("n_pulls", 0),
            "mean_reward": arm_data.get("mean_reward", 0.0),
            "alpha": arm_data.get("alpha", 1.0),
            "beta": arm_data.get("beta", 1.0),
        })
    arms_summary.sort(key=lambda a: a["mean_reward"], reverse=True)

    exploration_rate = (
        state.exploratory_decisions / state.total_decisions
        if state.total_decisions > 0 else 0.0
    )

    return {
        "enabled": state.config.get("enabled", True),
        "shadow_mode": state.config.get("shadow_mode", True),
        "policy": state.config.get("policy", "epsilon_greedy"),
        "current_epsilon": state.current_epsilon,
        "total_decisions": state.total_decisions,
        "exploratory_decisions": state.exploratory_decisions,
        "exploration_rate": round(exploration_rate, 4),
        "arms": arms_summary,
        "updated_at": state.updated_at,
    }


# ── Video Generation Settings ──────────────────────────────────


@router.get(
    "/video-settings",
    summary="Video auto-generation settings and spend",
)
async def get_video_settings_route(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Returns Replicate auto-generation settings and current month spend."""
    from app.services.video_budget_service import get_video_settings
    return await get_video_settings(db, tenant_id)


@router.put(
    "/video-settings",
    summary="Update video auto-generation settings",
)
async def update_video_settings_route(
    body: dict,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Toggle auto-Replicate and set monthly budget cap."""
    from app.services.video_budget_service import update_video_settings
    return await update_video_settings(db, tenant_id, body)

"""Video budget service — manage Replicate auto-generation settings and spend.

Settings stored in TenantModel.settings["video_generation"]:
{
    "auto_replicate_enabled": false,
    "monthly_budget_usd": 10.00,
    "current_month_spend_usd": 0.0,
    "spend_period": "2026-04"
}
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

ESTIMATED_COST_PER_CLIP = 0.25  # USD

DEFAULTS = {
    "auto_replicate_enabled": False,
    "monthly_budget_usd": 10.00,
    "current_month_spend_usd": 0.0,
    "spend_period": "",
}


async def get_video_settings(db: AsyncSession, tenant_id: uuid.UUID) -> dict:
    """Read video generation settings for a tenant, with defaults."""
    from amplify.db.models.tenant import TenantModel

    result = await db.execute(
        select(TenantModel).where(TenantModel.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        return dict(DEFAULTS)

    stored = (tenant.settings or {}).get("video_generation", {})
    return {**DEFAULTS, **stored}


async def can_generate_ai_video(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    estimated_cost: float = ESTIMATED_COST_PER_CLIP,
) -> tuple[bool, str]:
    """Check if auto-Replicate is enabled and budget allows.

    Returns (allowed, reason).
    """
    settings = await get_video_settings(db, tenant_id)

    if not settings.get("auto_replicate_enabled"):
        return False, "auto_replicate_enabled is off"

    budget = settings.get("monthly_budget_usd", 10.0)
    current_period = date.today().strftime("%Y-%m")
    stored_period = settings.get("spend_period", "")

    # If we're in a new month, spend resets
    if stored_period != current_period:
        spend = 0.0
    else:
        spend = settings.get("current_month_spend_usd", 0.0)

    if spend + estimated_cost > budget:
        return False, f"budget exceeded (${spend:.2f} + ${estimated_cost:.2f} > ${budget:.2f})"

    return True, "ok"


async def record_spend(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    cost_usd: float,
) -> float:
    """Record a Replicate generation cost. Returns new total spend.

    Handles month rollover automatically.
    """
    from amplify.db.models.tenant import TenantModel

    result = await db.execute(
        select(TenantModel).where(TenantModel.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        return 0.0

    settings = dict(tenant.settings or {})
    vg = dict(settings.get("video_generation", DEFAULTS))

    current_period = date.today().strftime("%Y-%m")
    if vg.get("spend_period", "") != current_period:
        vg["current_month_spend_usd"] = 0.0
        vg["spend_period"] = current_period

    vg["current_month_spend_usd"] = vg.get("current_month_spend_usd", 0.0) + cost_usd
    settings["video_generation"] = vg
    tenant.settings = settings
    await db.flush()

    logger.info(
        "Recorded Replicate spend for tenant %s: +$%.2f (total $%.2f / $%.2f)",
        tenant_id, cost_usd,
        vg["current_month_spend_usd"],
        vg.get("monthly_budget_usd", 10.0),
    )
    return vg["current_month_spend_usd"]


async def update_video_settings(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    updates: dict,
) -> dict:
    """Update video generation settings. Only updates provided keys."""
    from amplify.db.models.tenant import TenantModel

    result = await db.execute(
        select(TenantModel).where(TenantModel.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        return dict(DEFAULTS)

    settings = dict(tenant.settings or {})
    vg = dict(settings.get("video_generation", DEFAULTS))

    allowed_keys = {"auto_replicate_enabled", "monthly_budget_usd"}
    for key in allowed_keys:
        if key in updates:
            vg[key] = updates[key]

    settings["video_generation"] = vg
    tenant.settings = settings
    await db.flush()

    return vg

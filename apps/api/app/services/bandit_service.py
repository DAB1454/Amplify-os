"""Bandit service — load, save, and log contextual bandit per tenant.

Stores bandit state in TenantModel.settings["bandit_state"] JSON.
Logs decisions to LearningAuditLogModel for the intelligence dashboard.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def load_bandit(
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> "ContextualBandit":
    """Load a tenant's bandit from DB settings, or create a fresh one."""
    from amplify.db.models.tenant import TenantModel
    from amplify.learning.ranking.bandit import (
        BanditConfig,
        ContextualBandit,
        TenantBanditState,
    )
    from amplify.learning.ranking.post_ranker import PostRanker

    result = await db.execute(
        select(TenantModel).where(TenantModel.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()

    fallback = PostRanker()

    if tenant and tenant.settings and "bandit_state" in tenant.settings:
        try:
            state_data = tenant.settings["bandit_state"]
            state = TenantBanditState(
                tenant_id=str(tenant_id),
                config=state_data.get("config", {}),
                arms=state_data.get("arms", {}),
                total_decisions=state_data.get("total_decisions", 0),
                exploratory_decisions=state_data.get("exploratory_decisions", 0),
                current_epsilon=state_data.get("current_epsilon", 0.1),
            )
            bandit = state.to_bandit(fallback_ranker=fallback)
            logger.info(
                "Loaded bandit for tenant %s: %d arms, %d decisions",
                tenant_id, len(state.arms), state.total_decisions,
            )
            return bandit
        except Exception as exc:
            logger.warning("Failed to load bandit state for tenant %s: %s", tenant_id, exc)

    # Fresh bandit — shadow mode, epsilon-greedy
    config = BanditConfig(
        policy="epsilon_greedy",
        shadow_mode=True,
        enabled=True,
    )
    return ContextualBandit(fallback_ranker=fallback, config=config)


async def save_bandit(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    bandit: "ContextualBandit",
) -> None:
    """Persist bandit state to tenant settings."""
    from amplify.db.models.tenant import TenantModel
    from amplify.learning.ranking.bandit import TenantBanditState

    result = await db.execute(
        select(TenantModel).where(TenantModel.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        logger.warning("Cannot save bandit: tenant %s not found", tenant_id)
        return

    state = TenantBanditState.from_bandit(str(tenant_id), bandit)
    settings = dict(tenant.settings or {})
    settings["bandit_state"] = {
        "config": state.config,
        "arms": state.arms,
        "total_decisions": state.total_decisions,
        "exploratory_decisions": state.exploratory_decisions,
        "current_epsilon": state.current_epsilon,
        "updated_at": state.updated_at,
    }
    tenant.settings = settings
    await db.flush()
    logger.info(
        "Saved bandit for tenant %s: %d arms, %d decisions",
        tenant_id, len(state.arms), state.total_decisions,
    )


async def log_decision(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    decision: Any,
) -> None:
    """Write a bandit decision to the learning audit log."""
    from amplify.db.models.learning import LearningAuditLogModel

    log_data = decision.to_log_dict() if hasattr(decision, "to_log_dict") else {}

    entry = LearningAuditLogModel(
        tenant_id=tenant_id,
        action="bandit_selection",
        entity_type="post",
        entity_id=None,
        details=log_data,
        explanation=(
            f"Bandit ({log_data.get('policy_used', '?')}) "
            f"{'explored' if log_data.get('is_exploratory') else 'exploited'}"
            f"{' [shadow]' if log_data.get('is_shadow') else ''}"
        ),
    )
    db.add(entry)
    await db.flush()

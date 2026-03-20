"""Blended recommendation service.

Combines tenant-learned patterns with global priors for a unified
recommendation feed. Handles the cold-start case where a new tenant
has no local data and needs global defaults.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from amplify.learning.blending import BlendedRecommendation, BlendingEngine
from amplify.learning.config import LearningConfig
from app.services.global_prior_service import DBGlobalPriorService
from app.services.tenant_pattern_service import TenantPatternService


class BlendedRecommendationService:
    """Produces blended recommendations for a tenant."""

    def __init__(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        config: LearningConfig | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.config = config or LearningConfig()
        self.blender = BlendingEngine(self.config)
        self.pattern_svc = TenantPatternService(db, tenant_id)
        self.prior_svc = DBGlobalPriorService(db, self.config)

    async def get_blended_recommendations(
        self,
        category: str | None = None,
    ) -> dict[str, Any]:
        """Return blended recommendations with full data status.

        If the tenant has enough data, returns tenant-learned patterns
        blended with global priors. If not, returns global priors only
        with clear labeling.
        """
        # Get data status
        data_status = await self.pattern_svc.get_data_status()
        obs_count = data_status["feature_vectors"]

        # Get tenant recommendations (may be empty for cold-start)
        tenant_recs = await self.pattern_svc.get_recommendations()

        # Get global priors for cold-start/blending
        global_recs = await self.prior_svc.get_cold_start_recommendations(
            tenant_observation_count=obs_count,
            category=category,
        )

        # Blend
        blended = self.blender.blend(tenant_recs, global_recs, obs_count)

        # Build enriched data status
        is_cold = self.blender.is_cold_start(obs_count)
        maturity = self.blender.data_maturity_label(obs_count)
        tenant_weight = self.blender.compute_tenant_weight(obs_count)

        return {
            "recommendations": [r.to_dict() for r in blended],
            "data_status": {
                **data_status,
                "is_cold_start": is_cold,
                "maturity_label": maturity,
                "tenant_weight": round(tenant_weight, 4),
                "global_weight": round(1.0 - tenant_weight, 4),
            },
        }

    async def get_onboarding_recommendations(self) -> dict[str, Any]:
        """Get recommendations specifically for the onboarding flow.

        Always returns global priors for new tenants, grouped by
        category with human-friendly labels.
        """
        # For onboarding, always treat as cold-start
        global_recs = await self.prior_svc.get_cold_start_recommendations(
            tenant_observation_count=0,
        )

        data_status = await self.pattern_svc.get_data_status()
        obs_count = data_status["feature_vectors"]

        # Convert to blended format with source labels
        blended = self.blender.blend([], global_recs, obs_count)

        # Group by category for onboarding display
        grouped: dict[str, list[dict]] = {}
        for rec in blended:
            cat = rec.category or "general"
            grouped.setdefault(cat, [])
            grouped[cat].append(rec.to_dict())

        category_labels = {
            "posting_window": "When to Post",
            "cta_type": "Call-to-Action Style",
            "format_preference": "Content Format",
            "hook_family": "Hook Strategy",
            "general": "General Tips",
        }

        return {
            "categories": [
                {
                    "key": cat,
                    "label": category_labels.get(cat, cat.replace("_", " ").title()),
                    "recommendations": recs,
                }
                for cat, recs in grouped.items()
            ],
            "total": sum(len(v) for v in grouped.values()),
            "source": "global_prior",
            "note": (
                "These are smart defaults based on aggregate data from similar accounts. "
                "They'll be replaced by insights from your own data as you publish more."
            ),
        }

    async def migrate_cold_start_tenant(self) -> dict[str, Any]:
        """Initialize recommendations for an existing tenant with low data.

        Returns a summary of what was applied.
        """
        data_status = await self.pattern_svc.get_data_status()
        obs_count = data_status["feature_vectors"]

        if not self.blender.is_cold_start(obs_count):
            return {
                "migrated": False,
                "reason": "Tenant has sufficient data, no migration needed.",
                "observation_count": obs_count,
            }

        # Get global recommendations
        result = await self.get_blended_recommendations()
        rec_count = len(result["recommendations"])

        return {
            "migrated": True,
            "recommendations_available": rec_count,
            "observation_count": obs_count,
            "maturity_label": result["data_status"]["maturity_label"],
            "note": "Global priors are now available for this tenant's recommendations.",
        }

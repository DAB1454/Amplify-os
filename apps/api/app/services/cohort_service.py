"""DB-backed cohort service.

Bridges the pure-logic CohortEngine with SQLAlchemy models.
Handles persistence, versioning, and audit logging.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.learning.cohorts.engine import (
    CohortDefinition,
    CohortEngine,
    CohortPrior,
    CohortStats,
    TenantProfile,
)
from amplify.learning.config import LearningConfig


class CohortService:
    """Manages cohort definitions, assignments, and aggregation."""

    def __init__(
        self,
        db: AsyncSession,
        tenant_id: uuid.UUID,
        config: LearningConfig | None = None,
    ) -> None:
        self.db = db
        self.tenant_id = tenant_id
        self.config = config or LearningConfig()
        self.engine = CohortEngine(
            k_anonymity_threshold=self.config.privacy.k_anonymity_threshold,
        )

    # ── CRUD ──────────────────────────────────────────────────────

    async def create_definition(
        self,
        data: dict[str, Any],
        user_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Create a new cohort definition."""
        from amplify.db.models.learning import CohortDefinitionModel, LearningAuditLogModel

        defn = CohortDefinitionModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            name=data["name"],
            description=data.get("description", ""),
            criteria=data["criteria"],
            is_active=True,
        )
        self.db.add(defn)

        self.db.add(LearningAuditLogModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            action="cohort_definition.created",
            entity_type="cohort_definition",
            entity_id=defn.id,
            user_id=user_id,
            details={"name": defn.name, "criteria": defn.criteria},
        ))

        await self.db.flush()
        return self._model_to_dict(defn)

    async def get_definition(self, defn_id: uuid.UUID) -> dict[str, Any] | None:
        """Get a single cohort definition."""
        from amplify.db.models.learning import CohortDefinitionModel

        stmt = select(CohortDefinitionModel).where(
            CohortDefinitionModel.id == defn_id,
            CohortDefinitionModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        defn = result.scalar_one_or_none()
        return self._model_to_dict(defn) if defn else None

    async def list_definitions(self, active_only: bool = True) -> list[dict[str, Any]]:
        """List cohort definitions for this tenant."""
        from amplify.db.models.learning import CohortDefinitionModel

        stmt = select(CohortDefinitionModel).where(
            CohortDefinitionModel.tenant_id == self.tenant_id,
        )
        if active_only:
            stmt = stmt.where(CohortDefinitionModel.is_active == True)  # noqa: E712
        stmt = stmt.order_by(CohortDefinitionModel.created_at.desc())
        result = await self.db.execute(stmt)
        return [self._model_to_dict(d) for d in result.scalars().all()]

    async def update_definition(
        self,
        defn_id: uuid.UUID,
        updates: dict[str, Any],
        user_id: uuid.UUID | None = None,
    ) -> dict[str, Any] | None:
        """Update a cohort definition (creates a new version)."""
        from amplify.db.models.learning import CohortDefinitionModel, LearningAuditLogModel

        stmt = select(CohortDefinitionModel).where(
            CohortDefinitionModel.id == defn_id,
            CohortDefinitionModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        defn = result.scalar_one_or_none()
        if not defn:
            return None

        old_values = {"name": defn.name, "criteria": defn.criteria}

        if "name" in updates:
            defn.name = updates["name"]
        if "description" in updates:
            defn.description = updates["description"]
        if "criteria" in updates:
            defn.criteria = updates["criteria"]
        if "is_active" in updates:
            defn.is_active = updates["is_active"]

        # Reset member count since criteria may have changed
        if "criteria" in updates:
            defn.member_count = 0
            defn.last_computed_at = None

        self.db.add(LearningAuditLogModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            action="cohort_definition.updated",
            entity_type="cohort_definition",
            entity_id=defn.id,
            user_id=user_id,
            details={"old": old_values, "new": updates},
        ))

        await self.db.flush()

        # Re-query to get fresh attributes (avoids MissingGreenlet on lazy loads)
        await self.db.refresh(defn)
        return self._model_to_dict(defn)

    async def delete_definition(
        self,
        defn_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> bool:
        """Soft-delete a cohort definition."""
        from amplify.db.models.learning import CohortDefinitionModel, LearningAuditLogModel

        stmt = select(CohortDefinitionModel).where(
            CohortDefinitionModel.id == defn_id,
            CohortDefinitionModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        defn = result.scalar_one_or_none()
        if not defn:
            return False

        defn.is_active = False

        self.db.add(LearningAuditLogModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            action="cohort_definition.deleted",
            entity_type="cohort_definition",
            entity_id=defn.id,
            user_id=user_id,
            details={"name": defn.name},
        ))

        await self.db.flush()
        return True

    # ── Assignment ────────────────────────────────────────────────

    async def assign_tenant(
        self,
        profile: TenantProfile,
    ) -> list[dict[str, Any]]:
        """Assign a tenant to all matching cohort definitions.

        Returns the list of cohort definitions the tenant matches.
        """
        definitions = await self._load_definitions()
        assignments = self.engine.assign_tenants([profile], definitions)
        return [
            {"cohort_id": str(a.cohort_id), "cohort_name": a.cohort_name}
            for a in assignments
        ]

    # ── Aggregation ───────────────────────────────────────────────

    async def aggregate_cohort(
        self,
        defn_id: uuid.UUID,
        profiles: list[TenantProfile],
    ) -> CohortStats | None:
        """Aggregate outcomes for a specific cohort definition."""
        from amplify.db.models.learning import CohortDefinitionModel

        stmt = select(CohortDefinitionModel).where(
            CohortDefinitionModel.id == defn_id,
            CohortDefinitionModel.tenant_id == self.tenant_id,
        )
        result = await self.db.execute(stmt)
        model = result.scalar_one_or_none()
        if not model:
            return None

        defn = self._model_to_engine_defn(model)
        stats = self.engine.aggregate_cohort(defn, profiles)

        if stats:
            # Update cached member count
            model.member_count = stats.tenant_count
            model.last_computed_at = datetime.now(timezone.utc)
            await self.db.flush()

        return stats

    async def aggregate_all(
        self,
        profiles: list[TenantProfile],
    ) -> list[CohortStats]:
        """Aggregate all active cohort definitions."""
        definitions = await self._load_definitions()
        return self.engine.aggregate_all(definitions, profiles)

    # ── Cold-start priors ─────────────────────────────────────────

    async def get_cold_start_priors(
        self,
        tenant_profile: TenantProfile,
        all_profiles: list[TenantProfile],
        feature_names: list[str] | None = None,
    ) -> list[CohortPrior]:
        """Compute cold-start priors for a tenant from matching cohorts."""
        definitions = await self._load_definitions()
        return self.engine.compute_cold_start_priors(
            tenant_profile,
            definitions,
            all_profiles,
            feature_names=feature_names,
        )

    # ── Sample definitions ────────────────────────────────────────

    async def seed_sample_definitions(self) -> list[dict[str, Any]]:
        """Create sample cohort definitions for bootstrapping."""
        from amplify.learning.cohorts.engine import SAMPLE_COHORT_DEFINITIONS

        created = []
        for sample in SAMPLE_COHORT_DEFINITIONS:
            defn = await self.create_definition(sample)
            created.append(defn)
        return created

    # ── Helpers ────────────────────────────────────────────────────

    async def _load_definitions(self) -> list[CohortDefinition]:
        """Load active cohort definitions from DB as engine objects."""
        from amplify.db.models.learning import CohortDefinitionModel

        stmt = select(CohortDefinitionModel).where(
            CohortDefinitionModel.tenant_id == self.tenant_id,
            CohortDefinitionModel.is_active == True,  # noqa: E712
        )
        result = await self.db.execute(stmt)
        return [self._model_to_engine_defn(m) for m in result.scalars().all()]

    @staticmethod
    def _model_to_engine_defn(model) -> CohortDefinition:
        return CohortDefinition(
            id=model.id,
            name=model.name,
            description=model.description,
            criteria=model.criteria,
            is_active=model.is_active,
        )

    @staticmethod
    def _model_to_dict(model) -> dict[str, Any]:
        created_at = getattr(model, "created_at", None)
        updated_at = getattr(model, "updated_at", None)
        return {
            "id": str(model.id),
            "tenant_id": str(model.tenant_id),
            "name": model.name,
            "description": model.description,
            "criteria": model.criteria,
            "member_count": model.member_count,
            "last_computed_at": (
                model.last_computed_at.isoformat() if model.last_computed_at else None
            ),
            "is_active": model.is_active,
            "created_at": created_at.isoformat() if created_at else None,
            "updated_at": updated_at.isoformat() if updated_at else None,
        }

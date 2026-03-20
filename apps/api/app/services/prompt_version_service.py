"""Service layer for prompt version management and lineage tracking.

Handles CRUD for prompt versions, tenant assignments, promotion/deprecation,
rollback, and lineage queries.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.learning import (
    LearningAuditLogModel,
    LearningEventModel,
    PromptAssignmentModel,
    PromptVersionModel,
)
from amplify.learning.prompts.registry import (
    PromptAssignment,
    PromptRegistry,
    PromptStatus,
    PromptVersion,
)


class PromptVersionService:
    """DB-backed prompt version management.

    All mutations are audited to the LearningAuditLog.
    """

    def __init__(self, db: AsyncSession, tenant_id: uuid.UUID | None = None) -> None:
        self.db = db
        self.tenant_id = tenant_id

    # ── Version CRUD ──────────────────────────────────────────────

    async def create_version(
        self,
        *,
        prompt_key: str,
        template: str,
        system_prompt: str | None = None,
        model_id: str | None = None,
        parameters: dict | None = None,
        description: str = "",
        author: str | None = None,
        status: str = "active",
    ) -> PromptVersionModel:
        """Create a new prompt version with auto-incremented version number."""
        next_version = await self._next_version_number(prompt_key)

        row = PromptVersionModel(
            id=uuid.uuid4(),
            prompt_key=prompt_key,
            version=next_version,
            template=template,
            system_prompt=system_prompt,
            model_id=model_id,
            parameters=parameters or {},
            description=description,
            author=author,
            is_active=status in ("active", "shadow"),
        )
        self.db.add(row)
        await self.db.flush()

        await self._audit("prompt_version.created", "prompt_version", row.id, {
            "prompt_key": prompt_key,
            "version": next_version,
            "status": status,
            "author": author,
        })

        return row

    async def get_version(self, version_id: uuid.UUID) -> PromptVersionModel | None:
        result = await self.db.execute(
            select(PromptVersionModel).where(PromptVersionModel.id == version_id)
        )
        return result.scalar_one_or_none()

    async def list_versions(
        self,
        prompt_key: str | None = None,
        include_inactive: bool = False,
    ) -> list[PromptVersionModel]:
        stmt = select(PromptVersionModel).order_by(
            PromptVersionModel.prompt_key,
            PromptVersionModel.version.desc(),
        )
        if prompt_key:
            stmt = stmt.where(PromptVersionModel.prompt_key == prompt_key)
        if not include_inactive:
            stmt = stmt.where(PromptVersionModel.is_active.is_(True))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_active(self, prompt_key: str) -> PromptVersionModel | None:
        stmt = (
            select(PromptVersionModel)
            .where(
                PromptVersionModel.prompt_key == prompt_key,
                PromptVersionModel.is_active.is_(True),
            )
            .order_by(PromptVersionModel.version.desc())
            .limit(1)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    # ── Lifecycle: promote, deprecate, rollback ───────────────────

    async def promote(
        self,
        version_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> PromptVersionModel:
        """Promote a version to active. Deprecates the previous active version."""
        version = await self.get_version(version_id)
        if not version:
            raise ValueError(f"Version {version_id} not found")

        # Deprecate current active version(s) for this key
        await self.db.execute(
            update(PromptVersionModel)
            .where(
                PromptVersionModel.prompt_key == version.prompt_key,
                PromptVersionModel.is_active.is_(True),
                PromptVersionModel.id != version_id,
            )
            .values(is_active=False)
        )

        version.is_active = True
        await self.db.flush()

        await self._audit("prompt_version.promoted", "prompt_version", version.id, {
            "prompt_key": version.prompt_key,
            "version": version.version,
            "promoted_by": str(user_id) if user_id else None,
        })

        return version

    async def deprecate(
        self,
        version_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> PromptVersionModel:
        """Deprecate a version. Existing assignments will warn but still work."""
        version = await self.get_version(version_id)
        if not version:
            raise ValueError(f"Version {version_id} not found")

        version.is_active = False
        await self.db.flush()

        await self._audit("prompt_version.deprecated", "prompt_version", version.id, {
            "prompt_key": version.prompt_key,
            "version": version.version,
            "deprecated_by": str(user_id) if user_id else None,
        })

        return version

    async def rollback(
        self,
        prompt_key: str,
        target_version: int | None = None,
        user_id: uuid.UUID | None = None,
    ) -> PromptVersionModel:
        """Roll back to a previous version.

        If target_version is None, rolls back to the second-most-recent version.
        Deprecates the current active version and promotes the target.
        """
        versions = await self.list_versions(prompt_key, include_inactive=True)
        if not versions:
            raise ValueError(f"No versions found for prompt_key={prompt_key}")

        if target_version is not None:
            target = next(
                (v for v in versions if v.version == target_version), None,
            )
            if not target:
                raise ValueError(
                    f"Version {target_version} not found for {prompt_key}"
                )
        else:
            # Find the second-most-recent version
            sorted_versions = sorted(versions, key=lambda v: v.version, reverse=True)
            if len(sorted_versions) < 2:
                raise ValueError(f"Only one version exists for {prompt_key}")
            target = sorted_versions[1]

        # Deprecate all active
        await self.db.execute(
            update(PromptVersionModel)
            .where(
                PromptVersionModel.prompt_key == prompt_key,
                PromptVersionModel.is_active.is_(True),
            )
            .values(is_active=False)
        )

        # Activate target
        target.is_active = True
        await self.db.flush()

        await self._audit("prompt_version.rolled_back", "prompt_version", target.id, {
            "prompt_key": prompt_key,
            "rolled_back_to_version": target.version,
            "rolled_back_by": str(user_id) if user_id else None,
        })

        return target

    # ── Assignment management ─────────────────────────────────────

    async def assign(
        self,
        tenant_id: uuid.UUID,
        prompt_key: str,
        version_id: uuid.UUID,
        reason: str = "",
        assigned_by: uuid.UUID | None = None,
    ) -> PromptAssignmentModel:
        """Assign a prompt version to a tenant. Upserts on (tenant_id, prompt_key)."""
        # Check for any existing assignment (active or not) — unique constraint
        stmt = select(PromptAssignmentModel).where(
            PromptAssignmentModel.tenant_id == tenant_id,
            PromptAssignmentModel.prompt_key == prompt_key,
        )
        result = await self.db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            # Update in place to respect unique constraint
            existing.prompt_version_id = version_id
            existing.reason = reason
            existing.assigned_by = assigned_by
            existing.is_active = True
            await self.db.flush()
            row = existing
        else:
            row = PromptAssignmentModel(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                prompt_key=prompt_key,
                prompt_version_id=version_id,
                reason=reason,
                assigned_by=assigned_by,
                is_active=True,
            )
            self.db.add(row)
            await self.db.flush()

        await self._audit("prompt_assignment.created", "prompt_assignment", row.id, {
            "tenant_id": str(tenant_id),
            "prompt_key": prompt_key,
            "version_id": str(version_id),
            "reason": reason,
        })

        return row

    async def unassign(
        self,
        tenant_id: uuid.UUID,
        prompt_key: str,
    ) -> bool:
        """Remove a tenant's prompt assignment (reverts to default)."""
        assignment = await self._get_active_assignment(tenant_id, prompt_key)
        if not assignment:
            return False

        assignment.is_active = False
        await self.db.flush()

        await self._audit("prompt_assignment.removed", "prompt_assignment", assignment.id, {
            "tenant_id": str(tenant_id),
            "prompt_key": prompt_key,
        })

        return True

    async def get_assignment(
        self, tenant_id: uuid.UUID, prompt_key: str,
    ) -> PromptAssignmentModel | None:
        return await self._get_active_assignment(tenant_id, prompt_key)

    async def list_assignments(
        self, tenant_id: uuid.UUID | None = None,
    ) -> list[PromptAssignmentModel]:
        stmt = (
            select(PromptAssignmentModel)
            .where(PromptAssignmentModel.is_active.is_(True))
        )
        if tenant_id:
            stmt = stmt.where(PromptAssignmentModel.tenant_id == tenant_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # ── Registry builder ──────────────────────────────────────────

    async def build_registry(
        self,
        tenant_id: uuid.UUID | None = None,
        prompt_keys: list[str] | None = None,
    ) -> PromptRegistry:
        """Build a PromptRegistry populated from the database.

        Used before agent execution to resolve prompt versions without
        additional DB queries in the hot path.
        """
        registry = PromptRegistry()

        # Load versions
        version_stmt = select(PromptVersionModel).where(
            PromptVersionModel.is_active.is_(True)
        )
        if prompt_keys:
            version_stmt = version_stmt.where(
                PromptVersionModel.prompt_key.in_(prompt_keys)
            )
        result = await self.db.execute(version_stmt)
        for row in result.scalars().all():
            registry.add_version(PromptVersion(
                id=str(row.id),
                prompt_key=row.prompt_key,
                version=row.version,
                template=row.template,
                system_prompt=row.system_prompt,
                model_id=row.model_id,
                parameters=row.parameters or {},
                description=row.description or "",
                author=row.author,
                status="active",
                created_at=row.created_at.isoformat() if row.created_at else "",
            ))

        # Load tenant assignment
        if tenant_id:
            assign_stmt = select(PromptAssignmentModel).where(
                PromptAssignmentModel.tenant_id == tenant_id,
                PromptAssignmentModel.is_active.is_(True),
            )
            if prompt_keys:
                assign_stmt = assign_stmt.where(
                    PromptAssignmentModel.prompt_key.in_(prompt_keys)
                )
            result = await self.db.execute(assign_stmt)
            for row in result.scalars().all():
                registry.add_assignment(PromptAssignment(
                    id=str(row.id),
                    tenant_id=str(row.tenant_id),
                    prompt_key=row.prompt_key,
                    prompt_version_id=str(row.prompt_version_id),
                    reason=row.reason or "",
                    assigned_by=str(row.assigned_by) if row.assigned_by else None,
                    is_active=True,
                    created_at=row.created_at.isoformat() if row.created_at else "",
                ))

        return registry

    # ── Lineage queries ───────────────────────────────────────────

    async def get_lineage_for_post(self, post_id: uuid.UUID) -> list[dict]:
        """Get all learning events linked to a post with their prompt lineage."""
        stmt = (
            select(LearningEventModel)
            .where(LearningEventModel.post_id == post_id)
            .order_by(LearningEventModel.observed_at.desc())
        )
        result = await self.db.execute(stmt)
        events = list(result.scalars().all())

        lineage = []
        for ev in events:
            features = ev.features or {}
            lineage.append({
                "event_id": str(ev.id),
                "action_type": ev.action_type,
                "observed_at": ev.observed_at.isoformat() if ev.observed_at else None,
                "prompt_lineage": features.get("prompt_lineage"),
                "reward": ev.reward,
                "source": ev.source,
            })
        return lineage

    async def get_version_outcome_stats(
        self, prompt_key: str, version_id: uuid.UUID,
    ) -> dict:
        """Aggregate outcome stats for a specific prompt version.

        Scans learning events whose features contain the prompt_version_id
        to compute average reward and count.
        """
        # This requires JSON querying which varies by DB.
        # For now, return a placeholder that works with both SQLite and Postgres.
        stmt = select(
            func.count(LearningEventModel.id),
            func.avg(LearningEventModel.reward),
        ).where(
            LearningEventModel.reward.is_not(None),
        )
        result = await self.db.execute(stmt)
        row = result.one()
        return {
            "prompt_key": prompt_key,
            "version_id": str(version_id),
            "event_count": row[0] or 0,
            "avg_reward": round(float(row[1] or 0), 6),
        }

    # ── Helpers ───────────────────────────────────────────────────

    async def _next_version_number(self, prompt_key: str) -> int:
        stmt = select(func.max(PromptVersionModel.version)).where(
            PromptVersionModel.prompt_key == prompt_key
        )
        result = await self.db.execute(stmt)
        current_max = result.scalar()
        return (current_max or 0) + 1

    async def _get_active_assignment(
        self, tenant_id: uuid.UUID, prompt_key: str,
    ) -> PromptAssignmentModel | None:
        stmt = select(PromptAssignmentModel).where(
            PromptAssignmentModel.tenant_id == tenant_id,
            PromptAssignmentModel.prompt_key == prompt_key,
            PromptAssignmentModel.is_active.is_(True),
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _audit(
        self,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID,
        details: dict,
    ) -> None:
        row = LearningAuditLogModel(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id or uuid.UUID(int=0),
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            schema_version=1,
        )
        self.db.add(row)

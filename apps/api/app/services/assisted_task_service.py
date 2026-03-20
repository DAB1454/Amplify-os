"""Service layer for assisted-integration task workflows."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from amplify.db.models.assisted_task import AssistedTaskModel

from amplify.core.domain.assisted_task import (
    AssistedTask,
    AssistedTaskCreate,
    AssistedTaskUpdate,
    ChecklistItem,
    TaskStatus,
    URLValidation,
    bandcamp_release_checklist,
    bandcamp_update_checklist,
    linktree_new_release_checklist,
    linktree_sync_checklist,
)


class AssistedTaskService:
    def __init__(self, db: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id

    # ── CRUD ──────────────────────────────────────────────────────

    async def create_task(self, data: AssistedTaskCreate) -> dict:
        """Create a new assisted task."""
        task_id = uuid.uuid4()
        model = AssistedTaskModel(
            id=task_id,
            tenant_id=self.tenant_id,
            channel_id=data.channel_id,
            campaign_id=data.campaign_id,
            release_id=data.release_id,
            platform=data.platform,
            title=data.title,
            description=data.description,
            status=TaskStatus.PENDING.value,
            priority=data.priority.value,
            checklist=[item.model_dump() for item in data.checklist],
            url_validations=[],
            due_at=data.due_at,
            reminder_at=data.reminder_at,
            metadata_=data.metadata,
        )
        self.db.add(model)
        await self.db.commit()
        await self.db.refresh(model)
        return self._to_dict(model)

    async def get_task(self, task_id: uuid.UUID) -> dict | None:
        result = await self.db.execute(
            select(AssistedTaskModel).where(
                AssistedTaskModel.id == task_id,
                AssistedTaskModel.tenant_id == self.tenant_id,
            )
        )
        model = result.scalar_one_or_none()
        return self._to_dict(model) if model else None

    async def list_tasks(
        self,
        platform: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        query = select(AssistedTaskModel).where(
            AssistedTaskModel.tenant_id == self.tenant_id,
        )
        if platform:
            query = query.where(AssistedTaskModel.platform == platform)
        if status:
            query = query.where(AssistedTaskModel.status == status)
        query = query.order_by(AssistedTaskModel.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(query)
        return [self._to_dict(m) for m in result.scalars().all()]

    async def update_task(self, task_id: uuid.UUID, data: AssistedTaskUpdate) -> dict | None:
        result = await self.db.execute(
            select(AssistedTaskModel).where(
                AssistedTaskModel.id == task_id,
                AssistedTaskModel.tenant_id == self.tenant_id,
            )
        )
        model = result.scalar_one_or_none()
        if not model:
            return None

        updates = data.model_dump(exclude_none=True)
        for key, value in updates.items():
            if key == "status":
                value = value.value if hasattr(value, "value") else value
            if key == "priority":
                value = value.value if hasattr(value, "value") else value
            if key == "metadata":
                key = "metadata_"
            setattr(model, key, value)

        if data.status == TaskStatus.COMPLETED:
            model.completed_at = datetime.utcnow()

        await self.db.commit()
        await self.db.refresh(model)
        return self._to_dict(model)

    async def delete_task(self, task_id: uuid.UUID) -> bool:
        result = await self.db.execute(
            select(AssistedTaskModel).where(
                AssistedTaskModel.id == task_id,
                AssistedTaskModel.tenant_id == self.tenant_id,
            )
        )
        model = result.scalar_one_or_none()
        if not model:
            return False
        await self.db.delete(model)
        await self.db.commit()
        return True

    # ── Checklist operations ──────────────────────────────────────

    async def toggle_checklist_item(
        self, task_id: uuid.UUID, item_key: str, completed: bool
    ) -> dict | None:
        result = await self.db.execute(
            select(AssistedTaskModel).where(
                AssistedTaskModel.id == task_id,
                AssistedTaskModel.tenant_id == self.tenant_id,
            )
        )
        model = result.scalar_one_or_none()
        if not model:
            return None

        checklist = list(model.checklist or [])
        for item in checklist:
            if item.get("key") == item_key:
                item["is_completed"] = completed
                item["completed_at"] = datetime.utcnow().isoformat() if completed else None
                break

        model.checklist = checklist
        await self.db.commit()
        await self.db.refresh(model)
        return self._to_dict(model)

    # ── URL validation ────────────────────────────────────────────

    async def add_url_validation(
        self, task_id: uuid.UUID, validation: URLValidation
    ) -> dict | None:
        result = await self.db.execute(
            select(AssistedTaskModel).where(
                AssistedTaskModel.id == task_id,
                AssistedTaskModel.tenant_id == self.tenant_id,
            )
        )
        model = result.scalar_one_or_none()
        if not model:
            return None

        validations = list(model.url_validations or [])
        validations.append(validation.model_dump(mode="json"))
        model.url_validations = validations
        await self.db.commit()
        await self.db.refresh(model)
        return self._to_dict(model)

    # ── Template factories ────────────────────────────────────────

    async def create_bandcamp_release_task(
        self,
        release_id: uuid.UUID | None = None,
        campaign_id: uuid.UUID | None = None,
        channel_id: uuid.UUID | None = None,
        due_at: datetime | None = None,
    ) -> dict:
        return await self.create_task(AssistedTaskCreate(
            platform="bandcamp",
            title="Publish release on Bandcamp",
            description="Upload and publish your release on Bandcamp. Follow the checklist below.",
            channel_id=channel_id,
            campaign_id=campaign_id,
            release_id=release_id,
            checklist=bandcamp_release_checklist(),
            due_at=due_at,
        ))

    async def create_bandcamp_update_task(
        self,
        release_id: uuid.UUID | None = None,
        channel_id: uuid.UUID | None = None,
    ) -> dict:
        return await self.create_task(AssistedTaskCreate(
            platform="bandcamp",
            title="Update Bandcamp release",
            description="Update the description, pricing, or metadata for your Bandcamp release.",
            channel_id=channel_id,
            release_id=release_id,
            checklist=bandcamp_update_checklist(),
        ))

    async def create_linktree_sync_task(
        self,
        campaign_id: uuid.UUID | None = None,
        channel_id: uuid.UUID | None = None,
    ) -> dict:
        return await self.create_task(AssistedTaskCreate(
            platform="linktree",
            title="Sync Linktree links",
            description="Review and update your Linktree profile links.",
            channel_id=channel_id,
            campaign_id=campaign_id,
            checklist=linktree_sync_checklist(),
        ))

    async def create_linktree_release_task(
        self,
        release_id: uuid.UUID | None = None,
        campaign_id: uuid.UUID | None = None,
        channel_id: uuid.UUID | None = None,
    ) -> dict:
        return await self.create_task(AssistedTaskCreate(
            platform="linktree",
            title="Add release to Linktree",
            description="Add a tracked link for your new release to your Linktree profile.",
            channel_id=channel_id,
            campaign_id=campaign_id,
            release_id=release_id,
            checklist=linktree_new_release_checklist(),
        ))

    # ── Counts ────────────────────────────────────────────────────

    async def count_by_status(self) -> dict[str, int]:
        result = await self.db.execute(
            select(
                AssistedTaskModel.status,
                func.count(AssistedTaskModel.id),
            )
            .where(AssistedTaskModel.tenant_id == self.tenant_id)
            .group_by(AssistedTaskModel.status)
        )
        return {row[0]: row[1] for row in result.all()}

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _to_dict(model: AssistedTaskModel) -> dict:
        return {
            "id": str(model.id),
            "tenant_id": str(model.tenant_id),
            "channel_id": str(model.channel_id) if model.channel_id else None,
            "campaign_id": str(model.campaign_id) if model.campaign_id else None,
            "release_id": str(model.release_id) if model.release_id else None,
            "platform": model.platform,
            "title": model.title,
            "description": model.description,
            "status": model.status,
            "priority": model.priority,
            "checklist": model.checklist or [],
            "url_validations": model.url_validations or [],
            "due_at": model.due_at.isoformat() if model.due_at else None,
            "reminder_at": model.reminder_at.isoformat() if model.reminder_at else None,
            "reminder_sent": model.reminder_sent,
            "completed_at": model.completed_at.isoformat() if model.completed_at else None,
            "assigned_to": str(model.assigned_to) if model.assigned_to else None,
            "metadata": model.metadata_ or {},
            "created_at": model.created_at.isoformat() if model.created_at else None,
            "updated_at": model.updated_at.isoformat() if model.updated_at else None,
        }

"""API routes for assisted-integration task workflows."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id
from app.services.assisted_task_service import AssistedTaskService
from amplify.core.domain.assisted_task import (
    AssistedTaskCreate,
    AssistedTaskUpdate,
    ChecklistItem,
    TaskPriority,
    TaskStatus,
    URLValidation,
)

router = APIRouter(prefix="/assisted-tasks", tags=["assisted-tasks"])


def _get_service(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> AssistedTaskService:
    return AssistedTaskService(db, tenant_id)


# ── CRUD ──────────────────────────────────────────────────────────


@router.get("")
@router.get("/")
async def list_tasks(
    platform: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    svc: AssistedTaskService = Depends(_get_service),
):
    return await svc.list_tasks(platform=platform, status=status, limit=limit, offset=offset)


@router.post("/", status_code=201)
async def create_task(
    body: AssistedTaskCreate,
    svc: AssistedTaskService = Depends(_get_service),
):
    return await svc.create_task(body)


@router.get("/counts")
async def task_counts(
    svc: AssistedTaskService = Depends(_get_service),
):
    return await svc.count_by_status()


@router.get("/{task_id}")
async def get_task(
    task_id: uuid.UUID,
    svc: AssistedTaskService = Depends(_get_service),
):
    task = await svc.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.put("/{task_id}")
async def update_task(
    task_id: uuid.UUID,
    body: AssistedTaskUpdate,
    svc: AssistedTaskService = Depends(_get_service),
):
    task = await svc.update_task(task_id, body)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: uuid.UUID,
    svc: AssistedTaskService = Depends(_get_service),
):
    deleted = await svc.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")


# ── Checklist ─────────────────────────────────────────────────────


@router.post("/{task_id}/checklist/{item_key}/toggle")
async def toggle_checklist_item(
    task_id: uuid.UUID,
    item_key: str,
    completed: bool = Query(default=True),
    svc: AssistedTaskService = Depends(_get_service),
):
    task = await svc.toggle_checklist_item(task_id, item_key, completed)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


# ── URL Validation ────────────────────────────────────────────────


@router.post("/{task_id}/validate-url")
async def validate_url(
    task_id: uuid.UUID,
    url: str = Query(...),
    svc: AssistedTaskService = Depends(_get_service),
):
    """Validate a URL and record the result on the task."""
    from amplify.adapters.bandcamp.adapter import BandcampAdapter
    from amplify.adapters.linktree.adapter import LinktreeAdapter

    task = await svc.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    platform = task["platform"]

    if platform == "bandcamp":
        adapter = BandcampAdapter(dry_run=False)
        result = await adapter.validate_url(url)
    elif platform == "linktree":
        adapter = LinktreeAdapter(dry_run=False)
        results = await adapter.verify_links([url])
        result = results[0] if results else {"url": url, "is_valid": False, "error": "No result"}
    else:
        raise HTTPException(status_code=400, detail=f"URL validation not supported for {platform}")

    validation = URLValidation(
        url=result["url"],
        is_valid=result.get("is_valid", False),
        status_code=result.get("status_code"),
        title=result.get("title"),
        error=result.get("error"),
    )
    updated = await svc.add_url_validation(task_id, validation)
    return {"validation": validation.model_dump(mode="json"), "task": updated}


# ── Template factories ────────────────────────────────────────────


@router.post("/templates/bandcamp-release", status_code=201)
async def create_bandcamp_release_task(
    release_id: uuid.UUID | None = Query(default=None),
    campaign_id: uuid.UUID | None = Query(default=None),
    channel_id: uuid.UUID | None = Query(default=None),
    svc: AssistedTaskService = Depends(_get_service),
):
    return await svc.create_bandcamp_release_task(
        release_id=release_id, campaign_id=campaign_id, channel_id=channel_id,
    )


@router.post("/templates/bandcamp-update", status_code=201)
async def create_bandcamp_update_task(
    release_id: uuid.UUID | None = Query(default=None),
    channel_id: uuid.UUID | None = Query(default=None),
    svc: AssistedTaskService = Depends(_get_service),
):
    return await svc.create_bandcamp_update_task(
        release_id=release_id, channel_id=channel_id,
    )


@router.post("/templates/linktree-sync", status_code=201)
async def create_linktree_sync_task(
    campaign_id: uuid.UUID | None = Query(default=None),
    channel_id: uuid.UUID | None = Query(default=None),
    svc: AssistedTaskService = Depends(_get_service),
):
    return await svc.create_linktree_sync_task(
        campaign_id=campaign_id, channel_id=channel_id,
    )


@router.post("/templates/linktree-release", status_code=201)
async def create_linktree_release_task(
    release_id: uuid.UUID | None = Query(default=None),
    campaign_id: uuid.UUID | None = Query(default=None),
    channel_id: uuid.UUID | None = Query(default=None),
    svc: AssistedTaskService = Depends(_get_service),
):
    return await svc.create_linktree_release_task(
        release_id=release_id, campaign_id=campaign_id, channel_id=channel_id,
    )

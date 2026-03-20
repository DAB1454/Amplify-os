"""Prompt version management and lineage inspection routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id
from app.schemas.learning import (
    PromptAssignmentCreate,
    PromptAssignmentResponse,
    PromptVersionCreate,
    PromptVersionResponse,
)
from app.services.prompt_version_service import PromptVersionService

router = APIRouter(prefix="/prompts", tags=["prompts"])


def _get_service(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> PromptVersionService:
    return PromptVersionService(db, tenant_id)


# ── Versions ──────────────────────────────────────────────────────


@router.post(
    "/versions",
    response_model=PromptVersionResponse,
    status_code=201,
    summary="Create a new prompt version",
)
async def create_version(
    body: PromptVersionCreate,
    svc: PromptVersionService = Depends(_get_service),
):
    """Create a new prompt version with auto-incremented version number."""
    row = await svc.create_version(
        prompt_key=body.prompt_key,
        template=body.template,
        system_prompt=body.system_prompt,
        model_id=body.model_id,
        parameters=body.parameters,
        description=body.description,
        author=body.author,
    )
    await svc.db.commit()
    return row


@router.get(
    "/versions",
    response_model=list[PromptVersionResponse],
    summary="List prompt versions",
)
async def list_versions(
    prompt_key: str | None = Query(default=None),
    include_inactive: bool = Query(default=False),
    svc: PromptVersionService = Depends(_get_service),
):
    return await svc.list_versions(prompt_key, include_inactive)


@router.get(
    "/versions/{version_id}",
    response_model=PromptVersionResponse,
    summary="Get a specific prompt version",
)
async def get_version(
    version_id: uuid.UUID,
    svc: PromptVersionService = Depends(_get_service),
):
    row = await svc.get_version(version_id)
    if not row:
        raise HTTPException(status_code=404, detail="Version not found")
    return row


@router.post(
    "/versions/{version_id}/promote",
    response_model=PromptVersionResponse,
    summary="Promote a version to active",
)
async def promote_version(
    version_id: uuid.UUID,
    svc: PromptVersionService = Depends(_get_service),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Promote a version to active — deprecates the previous active version."""
    try:
        row = await svc.promote(version_id, user_id=tenant_id)
        await svc.db.commit()
        return row
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/versions/{version_id}/deprecate",
    response_model=PromptVersionResponse,
    summary="Deprecate a prompt version",
)
async def deprecate_version(
    version_id: uuid.UUID,
    svc: PromptVersionService = Depends(_get_service),
):
    try:
        row = await svc.deprecate(version_id)
        await svc.db.commit()
        return row
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/versions/{prompt_key}/rollback",
    response_model=PromptVersionResponse,
    summary="Roll back to a previous version",
)
async def rollback_version(
    prompt_key: str,
    target_version: int | None = Query(default=None),
    svc: PromptVersionService = Depends(_get_service),
):
    """Roll back to a previous version. If target_version is omitted,
    rolls back to the second-most-recent version."""
    try:
        row = await svc.rollback(prompt_key, target_version)
        await svc.db.commit()
        return row
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Assignments ───────────────────────────────────────────────────


@router.post(
    "/assignments",
    response_model=PromptAssignmentResponse,
    status_code=201,
    summary="Assign a prompt version to a tenant",
)
async def create_assignment(
    body: PromptAssignmentCreate,
    svc: PromptVersionService = Depends(_get_service),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    row = await svc.assign(
        tenant_id=tenant_id,
        prompt_key=body.prompt_key,
        version_id=body.prompt_version_id,
        reason=body.reason,
        assigned_by=body.assigned_by,
    )
    await svc.db.commit()
    return row


@router.get(
    "/assignments",
    response_model=list[PromptAssignmentResponse],
    summary="List prompt assignments",
)
async def list_assignments(
    svc: PromptVersionService = Depends(_get_service),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    return await svc.list_assignments(tenant_id)


@router.delete(
    "/assignments/{prompt_key}",
    summary="Remove a tenant's prompt assignment",
)
async def delete_assignment(
    prompt_key: str,
    svc: PromptVersionService = Depends(_get_service),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    removed = await svc.unassign(tenant_id, prompt_key)
    if not removed:
        raise HTTPException(status_code=404, detail="Assignment not found")
    await svc.db.commit()
    return {"status": "removed"}


# ── Lineage ───────────────────────────────────────────────────────


@router.get(
    "/lineage/post/{post_id}",
    summary="Get prompt lineage for a post",
)
async def get_post_lineage(
    post_id: uuid.UUID,
    svc: PromptVersionService = Depends(_get_service),
):
    return await svc.get_lineage_for_post(post_id)


@router.get(
    "/versions/{version_id}/stats",
    summary="Get outcome stats for a prompt version",
)
async def get_version_stats(
    version_id: uuid.UUID,
    prompt_key: str = Query(...),
    svc: PromptVersionService = Depends(_get_service),
):
    return await svc.get_version_outcome_stats(prompt_key, version_id)

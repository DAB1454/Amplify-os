"""Release CRUD routes with audit logging."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id, get_user_id, get_audit_service
from app.middleware.rbac import require_role
from app.schemas import ReleaseCreateRequest, ReleaseUpdateRequest, ReleaseResponse
from app.services.audit_service import AuditService
from amplify.db.models.release import ReleaseModel
from amplify.db.repository import BaseRepository

router = APIRouter(prefix="/releases", tags=["releases"])


@router.get("/", response_model=list[ReleaseResponse])
async def list_releases(
    artist_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """List releases, optionally filtered by artist_id."""
    repo = BaseRepository(db, ReleaseModel, tenant_id)
    filters = {}
    if artist_id is not None:
        filters["artist_id"] = artist_id
    return await repo.list(**filters)


@router.post("/", response_model=ReleaseResponse, status_code=status.HTTP_201_CREATED)
async def create_release(
    body: ReleaseCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Create a new release."""
    repo = BaseRepository(db, ReleaseModel, tenant_id)
    data = body.model_dump()
    # Map schema 'metadata' field to model 'metadata_' column
    if "metadata" in data:
        data["metadata_"] = data.pop("metadata")
    entity = await repo.create(**data)

    await audit.log(
        action="release.created",
        entity_type="release",
        entity_id=entity.id,
        user_id=user_id,
        changes=body.model_dump(),
    )

    return entity


@router.get("/{release_id}", response_model=ReleaseResponse)
async def get_release(
    release_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Get a release by ID."""
    repo = BaseRepository(db, ReleaseModel, tenant_id)
    entity = await repo.get(release_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Release not found")
    return entity


@router.put("/{release_id}", response_model=ReleaseResponse)
async def update_release(
    release_id: uuid.UUID,
    body: ReleaseUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Update a release by ID."""
    repo = BaseRepository(db, ReleaseModel, tenant_id)
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Map schema 'metadata' field to model 'metadata_' column
    if "metadata" in updates:
        updates["metadata_"] = updates.pop("metadata")

    entity = await repo.update(release_id, **updates)
    if entity is None:
        raise HTTPException(status_code=404, detail="Release not found")

    await audit.log(
        action="release.updated",
        entity_type="release",
        entity_id=entity.id,
        user_id=user_id,
        changes=body.model_dump(exclude_unset=True),
    )

    return entity


@router.delete(
    "/{release_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("admin"))],
)
async def delete_release(
    release_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Delete a release by ID. Admin+ only."""
    repo = BaseRepository(db, ReleaseModel, tenant_id)
    deleted = await repo.delete(release_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Release not found")

    await audit.log(
        action="release.deleted",
        entity_type="release",
        entity_id=release_id,
        user_id=user_id,
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)

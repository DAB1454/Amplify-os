"""Artist CRUD routes with audit logging."""

from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id, get_user_id, get_audit_service
from app.middleware.rbac import require_role
from app.schemas import ArtistCreateRequest, ArtistUpdateRequest, ArtistResponse
from app.services.audit_service import AuditService
from amplify.db.models.artist import ArtistModel
from amplify.db.repository import BaseRepository

router = APIRouter(prefix="/artists", tags=["artists"])


@router.get("", response_model=list[ArtistResponse])
@router.get("/", response_model=list[ArtistResponse])
async def list_artists(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """List all artists for the current tenant."""
    repo = BaseRepository(db, ArtistModel, tenant_id)
    return await repo.list()


@router.post("/", response_model=ArtistResponse, status_code=status.HTTP_201_CREATED)
async def create_artist(
    body: ArtistCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Create a new artist. Auto-generates slug from name."""
    repo = BaseRepository(db, ArtistModel, tenant_id)
    data = body.model_dump()
    data["slug"] = body.name.lower().replace(" ", "-")
    entity = await repo.create(**data)

    try:
        await audit.log(
            action="artist.created",
            entity_type="artist",
            entity_id=entity.id,
            user_id=user_id,
            changes=data,
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return entity


@router.get("/{artist_id}", response_model=ArtistResponse)
async def get_artist(
    artist_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Get an artist by ID."""
    repo = BaseRepository(db, ArtistModel, tenant_id)
    entity = await repo.get(artist_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Artist not found")
    return entity


@router.put("/{artist_id}", response_model=ArtistResponse)
async def update_artist(
    artist_id: uuid.UUID,
    body: ArtistUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Update an artist by ID."""
    repo = BaseRepository(db, ArtistModel, tenant_id)
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    entity = await repo.update(artist_id, **updates)
    if entity is None:
        raise HTTPException(status_code=404, detail="Artist not found")

    try:
        await audit.log(
            action="artist.updated",
            entity_type="artist",
            entity_id=entity.id,
            user_id=user_id,
            changes=updates,
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return entity


@router.delete(
    "/{artist_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role("admin"))],
)
async def delete_artist(
    artist_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Delete an artist by ID. Admin+ only."""
    repo = BaseRepository(db, ArtistModel, tenant_id)
    deleted = await repo.delete(artist_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Artist not found")

    try:
        await audit.log(
            action="artist.deleted",
            entity_type="artist",
            entity_id=artist_id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)

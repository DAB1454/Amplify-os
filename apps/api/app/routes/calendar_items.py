"""Calendar item CRUD routes."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id, get_user_id, get_audit_service
from app.schemas import (
    CalendarItemCreateRequest,
    CalendarItemUpdateRequest,
    CalendarItemResponse,
    CalendarImportResponse,
)
from app.services.audit_service import AuditService
from amplify.db.models.calendar_item import CalendarItemModel
from amplify.db.repository import BaseRepository

router = APIRouter(prefix="/calendar", tags=["calendar"])


@router.get("", response_model=list[CalendarItemResponse])
@router.get("/", response_model=list[CalendarItemResponse])
async def list_calendar_items(
    campaign_id: uuid.UUID | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """List calendar items, optionally filtered by campaign_id and date range."""
    stmt = (
        select(CalendarItemModel)
        .where(CalendarItemModel.tenant_id == tenant_id)
    )
    if campaign_id is not None:
        stmt = stmt.where(CalendarItemModel.campaign_id == campaign_id)
    if start_date is not None:
        stmt = stmt.where(CalendarItemModel.scheduled_date >= start_date)
    if end_date is not None:
        stmt = stmt.where(CalendarItemModel.scheduled_date <= end_date)

    stmt = stmt.order_by(CalendarItemModel.scheduled_date)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post("", response_model=CalendarItemResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=CalendarItemResponse, status_code=status.HTTP_201_CREATED)
async def create_calendar_item(
    body: CalendarItemCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Create a new calendar item."""
    repo = BaseRepository(db, CalendarItemModel, tenant_id)
    entity = await repo.create(**body.model_dump())

    await audit.log(
        action="calendar_item.created",
        entity_type="calendar_item",
        entity_id=entity.id,
        user_id=user_id,
        changes=body.model_dump(mode="json"),
    )

    return entity


@router.get("/{item_id}", response_model=CalendarItemResponse)
async def get_calendar_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Get a calendar item by ID."""
    repo = BaseRepository(db, CalendarItemModel, tenant_id)
    entity = await repo.get(item_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Calendar item not found")
    return entity


@router.put("/{item_id}", response_model=CalendarItemResponse)
async def update_calendar_item(
    item_id: uuid.UUID,
    body: CalendarItemUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Update a calendar item by ID."""
    repo = BaseRepository(db, CalendarItemModel, tenant_id)
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    entity = await repo.update(item_id, **updates)
    if entity is None:
        raise HTTPException(status_code=404, detail="Calendar item not found")

    await audit.log(
        action="calendar_item.updated",
        entity_type="calendar_item",
        entity_id=entity.id,
        user_id=user_id,
        changes=updates,
    )

    return entity


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_calendar_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Delete a calendar item by ID."""
    repo = BaseRepository(db, CalendarItemModel, tenant_id)
    deleted = await repo.delete(item_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Calendar item not found")

    await audit.log(
        action="calendar_item.deleted",
        entity_type="calendar_item",
        entity_id=item_id,
        user_id=user_id,
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{item_id}/complete", response_model=CalendarItemResponse)
async def complete_calendar_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Mark a calendar item as completed."""
    repo = BaseRepository(db, CalendarItemModel, tenant_id)
    entity = await repo.update(item_id, is_completed=True)
    if entity is None:
        raise HTTPException(status_code=404, detail="Calendar item not found")

    await audit.log(
        action="calendar_item.completed",
        entity_type="calendar_item",
        entity_id=entity.id,
        user_id=user_id,
        changes={"is_completed": True},
    )

    return entity


@router.post("/import", response_model=CalendarImportResponse)
async def import_calendar_csv(
    file: UploadFile = File(..., description="CSV file to import"),
    campaign_id: uuid.UUID | None = Form(default=None, description="Campaign to link items to"),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Import calendar items from a CSV file.

    Expected CSV columns: date, time, type, title, description, platform,
    caption, asset_ref, cta, track_ref, release_ref, campaign_ref
    """
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must be a .csv")

    content = await file.read()
    try:
        csv_text = content.decode("utf-8-sig")  # Handle BOM from Excel
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded")

    from app.services.calendar_import_service import CalendarImportService

    service = CalendarImportService(db, tenant_id)
    result = await service.import_csv(csv_text, campaign_id=campaign_id)

    await audit.log(
        action="calendar.imported",
        entity_type="calendar_item",
        user_id=user_id,
        changes={
            "filename": file.filename,
            "total_rows": result.total_rows,
            "imported": result.imported,
            "skipped": result.skipped,
        },
    )

    return result.to_dict()

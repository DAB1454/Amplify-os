"""Approval queue routes — full lifecycle with comments and audit trail."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id, get_user_id
from app.schemas import (
    ApprovalCheckResponse,
    ApprovalCommentRequest,
    ApprovalCommentResponse,
    ApprovalCountResponse,
    ApprovalCreateRequest,
    ApprovalDecisionRequest,
    ApprovalResubmitRequest,
    ApprovalResponse,
    AuditLogResponse,
)
from app.services.approval_service import ApprovalService

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _get_approval_service(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> ApprovalService:
    return ApprovalService(db, tenant_id)


# ── Check ─────────────────────────────────────────────────────────


@router.post("/check", response_model=ApprovalCheckResponse)
async def check_approval_needed(
    body: ApprovalCreateRequest,
    svc: ApprovalService = Depends(_get_approval_service),
):
    """Check whether an action requires approval (without creating one)."""
    result = svc.check_action(body.action_type, body.action_payload)
    return result


# ── Count ─────────────────────────────────────────────────────────


@router.get("/count", response_model=ApprovalCountResponse)
async def count_pending(
    svc: ApprovalService = Depends(_get_approval_service),
):
    """Return the count of pending approvals for badge display."""
    count = await svc.count_pending()
    return {"pending_count": count}


# ── List ──────────────────────────────────────────────────────────


@router.get("/pending", response_model=list[ApprovalResponse])
async def list_pending(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    svc: ApprovalService = Depends(_get_approval_service),
):
    """List all pending/resubmitted approvals."""
    return await svc.list_pending(offset=offset, limit=limit)


@router.get("/history", response_model=list[ApprovalResponse])
async def list_history(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None),
    svc: ApprovalService = Depends(_get_approval_service),
):
    """List all approvals (history) with optional status filter."""
    return await svc.list_history(offset=offset, limit=limit, status_filter=status)


# ── Create ────────────────────────────────────────────────────────


@router.post("/", response_model=ApprovalResponse, status_code=201)
async def create_approval(
    body: ApprovalCreateRequest,
    svc: ApprovalService = Depends(_get_approval_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
):
    """Create a new approval request."""
    approval = await svc.create_approval(
        action_type=body.action_type,
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        requested_by=user_id,
        notes=body.notes,
        action_payload=body.action_payload,
    )
    return approval


# ── Get ───────────────────────────────────────────────────────────


@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval(
    approval_id: uuid.UUID,
    svc: ApprovalService = Depends(_get_approval_service),
):
    """Get a single approval by ID."""
    try:
        return await svc.get(approval_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ── Decisions ─────────────────────────────────────────────────────


@router.post("/{approval_id}/approve", response_model=ApprovalResponse)
async def approve(
    approval_id: uuid.UUID,
    body: ApprovalDecisionRequest | None = None,
    svc: ApprovalService = Depends(_get_approval_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
):
    """Approve a pending request."""
    try:
        return await svc.approve(
            approval_id,
            reviewer_id=user_id,
            comment=body.comment if body else "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{approval_id}/reject", response_model=ApprovalResponse)
async def reject(
    approval_id: uuid.UUID,
    body: ApprovalDecisionRequest | None = None,
    svc: ApprovalService = Depends(_get_approval_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
):
    """Reject a pending request."""
    try:
        return await svc.reject(
            approval_id,
            reviewer_id=user_id,
            comment=body.comment if body else "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{approval_id}/request-revision", response_model=ApprovalResponse)
async def request_revision(
    approval_id: uuid.UUID,
    body: ApprovalDecisionRequest | None = None,
    svc: ApprovalService = Depends(_get_approval_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
):
    """Request changes before approving."""
    try:
        return await svc.request_revision(
            approval_id,
            reviewer_id=user_id,
            comment=body.comment if body else "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/{approval_id}/resubmit", response_model=ApprovalResponse)
async def resubmit(
    approval_id: uuid.UUID,
    body: ApprovalResubmitRequest | None = None,
    svc: ApprovalService = Depends(_get_approval_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
):
    """Resubmit after rejection or revision request."""
    try:
        return await svc.resubmit(
            approval_id,
            requester_id=user_id,
            notes=body.notes if body else "",
            updated_payload=body.action_payload if body else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# ── Comments ──────────────────────────────────────────────────────


@router.get("/{approval_id}/comments", response_model=list[ApprovalCommentResponse])
async def list_comments(
    approval_id: uuid.UUID,
    svc: ApprovalService = Depends(_get_approval_service),
):
    """List all comments on an approval."""
    try:
        return await svc.list_comments(approval_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post(
    "/{approval_id}/comments",
    response_model=ApprovalCommentResponse,
    status_code=201,
)
async def add_comment(
    approval_id: uuid.UUID,
    body: ApprovalCommentRequest,
    svc: ApprovalService = Depends(_get_approval_service),
    user_id: uuid.UUID | None = Depends(get_user_id),
):
    """Add a comment to an approval."""
    try:
        return await svc.add_comment(approval_id, user_id, body.body)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ── Audit trail ───────────────────────────────────────────────────


@router.get("/{approval_id}/audit", response_model=list[AuditLogResponse])
async def get_audit_trail(
    approval_id: uuid.UUID,
    svc: ApprovalService = Depends(_get_approval_service),
):
    """Get the full audit trail for an approval."""
    try:
        return await svc.get_audit_trail(approval_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

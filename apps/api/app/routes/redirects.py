"""Redirect routes — smart link CRUD and public redirect resolution."""

from __future__ import annotations

import hashlib
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id, get_user_id, get_audit_service
from app.schemas import RedirectCreateRequest, RedirectResponse
from app.services.audit_service import AuditService
from amplify.db.models.redirect import RedirectModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/links", tags=["links"])


def _generate_slug(url: str) -> str:
    """Deterministic 8-char slug from the target URL + a random salt."""
    raw = f"{url}-{uuid.uuid4().hex[:8]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


@router.post("", response_model=RedirectResponse, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=RedirectResponse, status_code=status.HTTP_201_CREATED)
async def create_redirect(
    body: RedirectCreateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Create a tracked redirect (smart link)."""
    slug = body.slug or _generate_slug(body.target_url)

    # Check slug uniqueness
    existing = await db.execute(
        select(RedirectModel).where(RedirectModel.slug == slug)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Slug already in use")

    redirect = RedirectModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        slug=slug,
        target_url=body.target_url,
        campaign_id=body.campaign_id,
    )
    db.add(redirect)
    await db.flush()

    try:
        await audit.log(
            action="redirect.created",
            entity_type="redirect",
            entity_id=redirect.id,
            user_id=user_id,
            changes={"slug": slug, "target_url": body.target_url},
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return redirect


@router.get("", response_model=list[RedirectResponse])
@router.get("/", response_model=list[RedirectResponse])
async def list_redirects(
    campaign_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """List all redirects for the tenant, optionally filtered by campaign."""
    stmt = select(RedirectModel).where(RedirectModel.tenant_id == tenant_id)
    if campaign_id is not None:
        stmt = stmt.where(RedirectModel.campaign_id == campaign_id)
    stmt = stmt.order_by(RedirectModel.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get("/{slug}/stats", response_model=RedirectResponse)
async def get_redirect_stats(
    slug: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Get redirect details and click count."""
    stmt = select(RedirectModel).where(
        RedirectModel.slug == slug,
        RedirectModel.tenant_id == tenant_id,
    )
    result = await db.execute(stmt)
    redirect = result.scalar_one_or_none()
    if redirect is None:
        raise HTTPException(status_code=404, detail="Redirect not found")
    return redirect


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_redirect(
    slug: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Delete a redirect."""
    stmt = select(RedirectModel).where(
        RedirectModel.slug == slug,
        RedirectModel.tenant_id == tenant_id,
    )
    result = await db.execute(stmt)
    redirect = result.scalar_one_or_none()
    if redirect is None:
        raise HTTPException(status_code=404, detail="Redirect not found")

    await db.delete(redirect)
    await db.flush()

    try:
        await audit.log(
            action="redirect.deleted",
            entity_type="redirect",
            entity_id=redirect.id,
            user_id=user_id,
        )
    except Exception as exc:
        logger.warning("Audit log failed (non-fatal): %s", exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Public redirect endpoint (no auth) ─────────────────────────

public_router = APIRouter(tags=["redirects"])


@public_router.get("/r/{slug}")
async def resolve_redirect(
    slug: str,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint: resolve a smart link and increment click count."""
    stmt = select(RedirectModel).where(RedirectModel.slug == slug)
    result = await db.execute(stmt)
    redirect = result.scalar_one_or_none()
    if redirect is None:
        raise HTTPException(status_code=404, detail="Link not found")

    # Increment click count
    await db.execute(
        update(RedirectModel)
        .where(RedirectModel.id == redirect.id)
        .values(click_count=RedirectModel.click_count + 1)
    )
    await db.flush()

    return Response(
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        headers={"Location": redirect.target_url},
    )

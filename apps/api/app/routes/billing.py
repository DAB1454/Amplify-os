"""Billing routes — plans, usage, subscription management."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, get_tenant_id, get_user_id, get_audit_service
from app.middleware.rbac import require_role
from app.services.audit_service import AuditService
from amplify.billing.enforcement import SubscriptionEnforcer
from amplify.billing.metering import MeteringService
from amplify.billing.plans import DEFAULT_PLANS, get_plan
from amplify.billing.stripe_client import StubPaymentProvider
from amplify.db.models.tenant import TenantModel

router = APIRouter(prefix="/billing", tags=["billing"])

# Singleton metering and payment provider (swap for Stripe in production)
_metering = MeteringService()
_payment = StubPaymentProvider()


def _get_metering() -> MeteringService:
    return _metering


# ── Plans ─────────────────────────────────────────────────────────


@router.get("/plans")
async def list_plans():
    """Return all available billing plans."""
    return [
        {
            "id": p.id,
            "name": p.name,
            "tier": p.tier,
            "price_monthly": p.price_monthly,
            "price_yearly": p.price_yearly,
            "features": p.features,
            "limits": p.limits.model_dump(),
        }
        for p in DEFAULT_PLANS
    ]


# ── Usage ─────────────────────────────────────────────────────────


@router.get("/usage")
async def get_usage(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Return current usage vs plan limits for the tenant."""
    tenant = await _get_tenant(db, tenant_id)
    enforcer = SubscriptionEnforcer(db, tenant_id, tenant.plan_tier, _metering)
    return await enforcer.get_usage_summary()


@router.get("/can-add/{resource}")
async def check_can_add(
    resource: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Check if the tenant can add one more of a given resource."""
    tenant = await _get_tenant(db, tenant_id)
    enforcer = SubscriptionEnforcer(db, tenant_id, tenant.plan_tier, _metering)

    checks = {
        "artist": enforcer.can_add_artist,
        "channel": enforcer.can_add_channel,
        "campaign": enforcer.can_add_campaign,
        "team_member": enforcer.can_add_team_member,
        "post": enforcer.can_schedule_post,
        "render": enforcer.can_render_media,
    }
    check_fn = checks.get(resource)
    if check_fn is None:
        raise HTTPException(status_code=400, detail=f"Unknown resource: {resource}")

    result = await check_fn()
    return {
        "allowed": result.allowed,
        "metric": result.metric,
        "current": result.current,
        "limit": result.limit,
        "message": result.message,
    }


# ── Subscription ─────────────────────────────────────────────────


@router.get("/subscription")
async def get_subscription(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Return the tenant's current subscription details."""
    tenant = await _get_tenant(db, tenant_id)
    plan = get_plan(tenant.plan_tier)
    return {
        "plan_tier": tenant.plan_tier,
        "plan_name": plan.name if plan else tenant.plan_tier,
        "stripe_customer_id": tenant.stripe_customer_id,
        "stripe_subscription_id": tenant.stripe_subscription_id,
    }


@router.post("/checkout")
async def create_checkout(
    plan_tier: str = Query(...),
    billing_period: str = Query(default="monthly"),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Create a Stripe checkout session for plan upgrade."""
    plan = get_plan(plan_tier)
    if plan is None:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan_tier}")

    tenant = await _get_tenant(db, tenant_id)

    # Create or reuse Stripe customer
    customer_id = tenant.stripe_customer_id
    if not customer_id:
        customer = await _payment.create_customer(
            email=f"tenant-{tenant.slug}@amplify-os.dev",
            name=tenant.name,
            tenant_id=str(tenant_id),
        )
        tenant.stripe_customer_id = customer.customer_id
        customer_id = customer.customer_id

    price_id = (
        plan.stripe_price_id_yearly if billing_period == "yearly"
        else plan.stripe_price_id_monthly
    ) or plan.tier

    session = await _payment.create_checkout_session(
        customer_id=customer_id,
        price_id=price_id,
        success_url="http://localhost:3000/billing?success=true",
        cancel_url="http://localhost:3000/billing?canceled=true",
    )

    await audit.log(
        action="billing.checkout_created",
        entity_type="tenant",
        entity_id=tenant_id,
        user_id=user_id,
        changes={"plan_tier": plan_tier, "billing_period": billing_period},
    )

    return {"checkout_url": session.url, "session_id": session.session_id}


@router.post(
    "/change-plan",
    dependencies=[Depends(require_role("owner"))],
)
async def change_plan(
    plan_tier: str = Query(...),
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
    audit: AuditService = Depends(get_audit_service),
):
    """Change the tenant's plan tier (owner only)."""
    plan = get_plan(plan_tier)
    if plan is None:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {plan_tier}")

    tenant = await _get_tenant(db, tenant_id)
    old_tier = tenant.plan_tier
    tenant.plan_tier = plan_tier

    await audit.log(
        action="billing.plan_changed",
        entity_type="tenant",
        entity_id=tenant_id,
        user_id=user_id,
        changes={"from": old_tier, "to": plan_tier},
    )

    return {"plan_tier": plan_tier, "plan_name": plan.name}


@router.post("/portal")
async def create_portal_session(
    db: AsyncSession = Depends(get_db),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Create a Stripe billing portal session."""
    tenant = await _get_tenant(db, tenant_id)
    if not tenant.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No billing account found")

    session = await _payment.create_billing_portal_session(
        customer_id=tenant.stripe_customer_id,
        return_url="http://localhost:3000/billing",
    )
    return {"portal_url": session.url}


# ── Helpers ───────────────────────────────────────────────────────


async def _get_tenant(db: AsyncSession, tenant_id: uuid.UUID) -> TenantModel:
    result = await db.execute(
        select(TenantModel).where(TenantModel.id == tenant_id)
    )
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant

"""Stripe-compatible billing abstraction.

Provides a protocol-based interface so the billing system can work
with Stripe in production or a stub in development/testing.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, Field


class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    TRIALING = "trialing"
    INCOMPLETE = "incomplete"
    PAUSED = "paused"


class CustomerInfo(BaseModel):
    customer_id: str
    email: str
    name: str = ""
    metadata: dict[str, str] = Field(default_factory=dict)


class SubscriptionInfo(BaseModel):
    subscription_id: str
    customer_id: str
    plan_tier: str
    status: SubscriptionStatus
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool = False
    trial_end: datetime | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class CheckoutSession(BaseModel):
    session_id: str
    url: str
    customer_id: str = ""
    plan_tier: str = ""


class BillingPortalSession(BaseModel):
    url: str


class PaymentProvider(Protocol):
    """Interface for payment provider integrations."""

    async def create_customer(
        self, email: str, name: str, tenant_id: str
    ) -> CustomerInfo:
        ...

    async def create_checkout_session(
        self,
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        ...

    async def create_billing_portal_session(
        self, customer_id: str, return_url: str
    ) -> BillingPortalSession:
        ...

    async def get_subscription(
        self, subscription_id: str
    ) -> SubscriptionInfo:
        ...

    async def cancel_subscription(
        self, subscription_id: str, at_period_end: bool = True
    ) -> SubscriptionInfo:
        ...

    async def update_subscription(
        self, subscription_id: str, new_price_id: str
    ) -> SubscriptionInfo:
        ...

    async def handle_webhook(
        self, payload: bytes, signature: str
    ) -> dict[str, Any]:
        ...


class StubPaymentProvider:
    """In-memory payment provider for development and testing.

    Simulates Stripe's behavior without requiring API keys.
    """

    def __init__(self) -> None:
        self._customers: dict[str, CustomerInfo] = {}
        self._subscriptions: dict[str, SubscriptionInfo] = {}
        self._counter = 0

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self._counter:06d}"

    async def create_customer(
        self, email: str, name: str, tenant_id: str
    ) -> CustomerInfo:
        cid = self._next_id("cus_stub")
        customer = CustomerInfo(
            customer_id=cid,
            email=email,
            name=name,
            metadata={"tenant_id": tenant_id},
        )
        self._customers[cid] = customer
        return customer

    async def create_checkout_session(
        self,
        customer_id: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        sid = self._next_id("cs_stub")
        return CheckoutSession(
            session_id=sid,
            url=f"https://stub.checkout.example.com/{sid}",
            customer_id=customer_id,
            plan_tier=price_id,
        )

    async def create_billing_portal_session(
        self, customer_id: str, return_url: str
    ) -> BillingPortalSession:
        return BillingPortalSession(
            url=f"https://stub.billing.example.com/portal/{customer_id}"
        )

    async def get_subscription(
        self, subscription_id: str
    ) -> SubscriptionInfo:
        sub = self._subscriptions.get(subscription_id)
        if sub is None:
            raise ValueError(f"Subscription {subscription_id} not found")
        return sub

    async def cancel_subscription(
        self, subscription_id: str, at_period_end: bool = True
    ) -> SubscriptionInfo:
        sub = self._subscriptions.get(subscription_id)
        if sub is None:
            raise ValueError(f"Subscription {subscription_id} not found")
        if at_period_end:
            sub.cancel_at_period_end = True
        else:
            sub.status = SubscriptionStatus.CANCELED
        return sub

    async def update_subscription(
        self, subscription_id: str, new_price_id: str
    ) -> SubscriptionInfo:
        sub = self._subscriptions.get(subscription_id)
        if sub is None:
            raise ValueError(f"Subscription {subscription_id} not found")
        sub.plan_tier = new_price_id
        return sub

    async def handle_webhook(
        self, payload: bytes, signature: str
    ) -> dict[str, Any]:
        return {"type": "stub.event", "data": {}}

    # ── Test helpers ──────────────────────────────────────────────

    def add_subscription(self, sub: SubscriptionInfo) -> None:
        """Manually add a subscription for testing."""
        self._subscriptions[sub.subscription_id] = sub

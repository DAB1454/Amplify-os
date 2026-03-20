"""Amplify-OS billing — plans, metering, enforcement, and Stripe abstraction."""

from amplify.billing.enforcement import EnforcementResult, SubscriptionEnforcer
from amplify.billing.metering import (
    ALL_METRICS,
    METRIC_AI_GENERATIONS,
    METRIC_API_CALLS,
    METRIC_MEDIA_RENDERS,
    METRIC_POSTS_SCHEDULED,
    LimitExceeded,
    MeteringService,
)
from amplify.billing.plans import (
    ALL_TIERS,
    BillingPlan,
    DEFAULT_PLANS,
    PlanLimits,
    PlanTier,
    get_plan,
    get_plan_limits,
)
from amplify.billing.stripe_client import (
    BillingPortalSession,
    CheckoutSession,
    CustomerInfo,
    PaymentProvider,
    StubPaymentProvider,
    SubscriptionInfo,
    SubscriptionStatus,
)

__all__ = [
    # Plans
    "BillingPlan", "PlanLimits", "PlanTier", "ALL_TIERS",
    "DEFAULT_PLANS", "get_plan", "get_plan_limits",
    # Metering
    "MeteringService", "LimitExceeded", "ALL_METRICS",
    "METRIC_POSTS_SCHEDULED", "METRIC_AI_GENERATIONS",
    "METRIC_MEDIA_RENDERS", "METRIC_API_CALLS",
    # Enforcement
    "SubscriptionEnforcer", "EnforcementResult",
    # Stripe
    "PaymentProvider", "StubPaymentProvider",
    "SubscriptionInfo", "SubscriptionStatus",
    "CustomerInfo", "CheckoutSession", "BillingPortalSession",
]

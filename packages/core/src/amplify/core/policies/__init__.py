"""Amplify-OS core policies — guardrails enforced at runtime."""

from amplify.core.policies.anti_spam import AntiSpamPolicy
from amplify.core.policies.brand_safety import BrandSafetyPolicy
from amplify.core.policies.engine import (
    ActionContext,
    Decision,
    PolicyEngine,
    PolicyResult,
    PolicyVerdict,
    create_default_engine,
)
from amplify.core.policies.integrity import IntegrityPolicy, IntegrityResult, IntegrityViolation
from amplify.core.policies.rate_limits import PlatformRateLimits, get_rate_limit
from amplify.core.policies.rights_guard import RightsGuard

__all__ = [
    "AntiSpamPolicy",
    "BrandSafetyPolicy",
    # Policy engine
    "PolicyEngine",
    "PolicyResult",
    "PolicyVerdict",
    "ActionContext",
    "Decision",
    "create_default_engine",
    # Integrity
    "IntegrityPolicy",
    "IntegrityResult",
    "IntegrityViolation",
    # Rate limits
    "PlatformRateLimits",
    "RightsGuard",
    "get_rate_limit",
]
